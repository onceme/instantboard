"""Unit tests for the layer-2 Redis sliding-window rate limiter
(security.md §3.3): route tiers, bucket key composition, enforcement
boundaries, exemptions, fail-open and window recovery.
"""

import contextlib
import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.core.middleware import RATE_LIMIT_WINDOW_SECONDS, RateLimitMiddleware, _route_class
from app.core.security import create_access_token

DEFAULT_IP = "203.0.113.7"  # TEST-NET-3, guaranteed unroutable
OTHER_IP = "198.51.100.23"
TENANT_A = str(uuid.uuid4())
TENANT_B = str(uuid.uuid4())


def _token(tenant_id: str) -> str:
    return create_access_token(
        {"sub": str(uuid.uuid4()), "tenant_id": tenant_id, "role": "member", "provider": "github"}
    )


def _request(path="/api/v1/categories", headers=None, client_ip=DEFAULT_IP, query_params=None, xff=None):
    request = MagicMock()
    request.url.path = path
    headers = dict(headers or {})
    if xff:
        headers["x-forwarded-for"] = xff
    request.headers = headers
    request.client.host = client_ip
    request.query_params = dict(query_params or {})
    return request


async def _dispatch(request, counts=None, fixed_count=1, **settings_overrides):
    """Run one dispatch with the sliding-window helper mocked.

    ``counts`` feeds a per-call side effect list; ``fixed_count`` returns the
    same window population every time. Extra keyword args patch attributes on
    the shared settings object for the duration of the call.
    """
    middleware = RateLimitMiddleware(app=MagicMock())
    response = MagicMock()
    call_next = AsyncMock(return_value=response)
    counter = AsyncMock(side_effect=list(counts)) if counts is not None else AsyncMock(return_value=fixed_count)
    patchers = [patch("app.core.middleware.redis_sliding_window_count", new=counter)]
    patchers += [patch.object(settings, name, value) for name, value in settings_overrides.items()]
    with contextlib.ExitStack() as stack:
        for patcher in patchers:
            stack.enter_context(patcher)
        result = await middleware.dispatch(request, call_next)
    return result, response, call_next, counter


def _assert_rate_limited(result):
    assert result.status_code == 429
    assert result.headers["Retry-After"] == str(RATE_LIMIT_WINDOW_SECONDS)
    assert json.loads(result.body) == {
        "success": False,
        "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded", "details": None},
    }


class TestRouteClassification:
    @pytest.mark.parametrize(
        "path,expected",
        [
            # auth tier: everything under /api/v1/auth
            ("/api/v1/auth", "auth"),
            ("/api/v1/auth/login", "auth"),
            ("/api/v1/auth/admin/login", "auth"),
            ("/api/v1/auth/sso/google", "auth"),
            # search tier: the two interactive search endpoints
            ("/api/v1/finance/search", "search"),
            ("/api/v1/finance/search/deep", "search"),
            ("/api/v1/tech/search", "search"),
            # default tier: any other /api/v1 route
            ("/api/v1/categories", "default"),
            ("/api/v1/finance/quotes/AAPL", "default"),
            ("/api/v1/tech/topics", "default"),
            ("/api/v1/authoritative", "default"),  # not the auth prefix
            ("/api/v1/search", "default"),  # not the finance/tech search paths
            # exempt: health probes
            ("/api/v1/health", None),
            ("/api/v1/health/detail", None),
            # exempt: SSE long-lived connections
            ("/api/v1/stream", None),
            ("/api/v1/stream/all", None),
            # exempt: anything outside /api/v1
            ("/", None),
            ("/healthz", None),
            ("/api/docs", None),
            ("/api/v1", None),
        ],
    )
    def test_route_class(self, path, expected):
        assert _route_class(path) == expected


class TestBucketKeyComposition:
    async def test_anonymous_bucket_without_token(self):
        _result, _response, _call_next, counter = await _dispatch(_request())
        assert counter.call_args.args[0] == f"rate:anon:{DEFAULT_IP}:default"

    async def test_tenant_bucket_from_bearer_token(self):
        request = _request(headers={"authorization": f"Bearer {_token(TENANT_A)}"})
        _result, _response, _call_next, counter = await _dispatch(request)
        assert counter.call_args.args[0] == f"rate:{TENANT_A}:{DEFAULT_IP}:default"

    async def test_tenant_bucket_from_sse_query_token(self):
        # EventSource cannot send headers, so SSE requests carry the token as
        # a query parameter — it must bucket identically.
        request = _request(query_params={"token": _token(TENANT_A)})
        _result, _response, _call_next, counter = await _dispatch(request)
        assert counter.call_args.args[0] == f"rate:{TENANT_A}:{DEFAULT_IP}:default"

    async def test_garbage_token_falls_back_to_anon(self):
        request = _request(headers={"authorization": "Bearer not.a.jwt"})
        _result, _response, _call_next, counter = await _dispatch(request)
        assert counter.call_args.args[0] == f"rate:anon:{DEFAULT_IP}:default"

    async def test_non_uuid_tenant_claim_falls_back_to_anon(self):
        # A forged non-UUID tenant claim must not reach the Redis key layout.
        request = _request(headers={"authorization": f"Bearer {_token('evil:colon:tenant')}"})
        _result, _response, _call_next, counter = await _dispatch(request)
        assert counter.call_args.args[0] == f"rate:anon:{DEFAULT_IP}:default"

    async def test_client_ip_uses_rightmost_xff_hop(self):
        request = _request(xff=f"10.0.0.1, {OTHER_IP}")
        _result, _response, _call_next, counter = await _dispatch(request)
        assert counter.call_args.args[0] == f"rate:anon:{OTHER_IP}:default"

    async def test_route_class_is_part_of_the_key(self):
        for path, tier in [("/api/v1/auth/login", "auth"), ("/api/v1/tech/search", "search")]:
            _result, _response, _call_next, counter = await _dispatch(_request(path=path))
            assert counter.call_args.args[0] == f"rate:anon:{DEFAULT_IP}:{tier}"


class TestEnforcementBoundaries:
    async def test_under_limit_passes(self):
        result, response, call_next, _counter = await _dispatch(
            _request(), fixed_count=1, rate_limit_per_minute=60, rate_limit_burst=10
        )
        assert result is response
        call_next.assert_awaited_once()

    async def test_count_at_limit_passes(self):
        # The window count includes the current request: exactly `limit`
        # requests inside the window is still allowed.
        result, response, call_next, _counter = await _dispatch(
            _request(), fixed_count=60, rate_limit_per_minute=60, rate_limit_burst=0
        )
        assert result is response
        call_next.assert_awaited_once()

    async def test_count_over_limit_returns_429_envelope(self):
        result, _response, call_next, _counter = await _dispatch(
            _request(), fixed_count=61, rate_limit_per_minute=60, rate_limit_burst=0
        )
        _assert_rate_limited(result)
        call_next.assert_not_awaited()

    async def test_burst_headroom_boundary(self):
        # limit + burst requests inside the window pass; the next one is 429.
        passed, response, call_next, _counter = await _dispatch(
            _request(), fixed_count=14, rate_limit_per_minute=10, rate_limit_burst=4
        )
        assert passed is response
        call_next.assert_awaited_once()

        rejected, _response, call_next2, _counter2 = await _dispatch(
            _request(), fixed_count=15, rate_limit_per_minute=10, rate_limit_burst=4
        )
        _assert_rate_limited(rejected)
        call_next2.assert_not_awaited()

    async def test_auth_route_uses_auth_limit(self):
        request = _request(path="/api/v1/auth/admin/login")
        # count 4 is over the auth limit but far below the default limit.
        rejected, _response, _call_next, _counter = await _dispatch(
            request, fixed_count=4, rate_limit_auth_per_minute=3, rate_limit_per_minute=100, rate_limit_burst=0
        )
        _assert_rate_limited(rejected)

        passed, response, call_next, _counter = await _dispatch(
            _request(), fixed_count=4, rate_limit_auth_per_minute=3, rate_limit_per_minute=100, rate_limit_burst=0
        )
        assert passed is response
        call_next.assert_awaited_once()

    async def test_search_route_uses_search_limit(self):
        for path in ("/api/v1/finance/search", "/api/v1/tech/search"):
            rejected, _response, _call_next, _counter = await _dispatch(
                _request(path=path),
                fixed_count=6,
                rate_limit_search_per_minute=5,
                rate_limit_per_minute=100,
                rate_limit_burst=0,
            )
            _assert_rate_limited(rejected)


class TestExemptionsAndSwitch:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/health",
            "/api/v1/health/detail",
            "/api/v1/stream",
            "/api/v1/stream/finance",
            "/",
            "/healthz",
            "/api/docs",
        ],
    )
    async def test_exempt_paths_never_touch_redis(self, path):
        result, response, call_next, counter = await _dispatch(_request(path=path), rate_limit_per_minute=0)
        assert result is response
        call_next.assert_awaited_once()
        counter.assert_not_awaited()

    async def test_disabled_switch_passes_without_redis(self):
        result, response, call_next, counter = await _dispatch(
            _request(), rate_limit_enabled=False, rate_limit_per_minute=0, rate_limit_burst=0
        )
        assert result is response
        call_next.assert_awaited_once()
        counter.assert_not_awaited()

    async def test_enabled_by_default(self):
        # Sanity: with the shipped defaults a counted request is evaluated.
        _result, _response, _call_next, counter = await _dispatch(_request(), fixed_count=1)
        counter.assert_awaited_once()


class TestFailOpen:
    async def test_redis_error_passes_the_request(self):
        middleware = RateLimitMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        with patch(
            "app.core.middleware.redis_sliding_window_count",
            new=AsyncMock(side_effect=ConnectionError("redis down")),
        ):
            result = await middleware.dispatch(_request(), call_next)
        assert result is response
        call_next.assert_awaited_once()

    async def test_redis_error_logs_debug(self, caplog):
        middleware = RateLimitMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock())
        with (
            patch(
                "app.core.middleware.redis_sliding_window_count",
                new=AsyncMock(side_effect=ConnectionError("redis down")),
            ),
            caplog.at_level("DEBUG", logger="instantboard"),
        ):
            await middleware.dispatch(_request(), call_next)
        assert any("failing open" in record.message for record in caplog.records)
        call_next.assert_awaited_once()


class _FakeClock:
    def __init__(self, start=1_700_000_000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestSlidingWindowBehavior:
    """End-to-end dispatch through the REAL redis_sliding_window_count helper
    against the MockRedis ZSET implementation."""

    def _patch_helper_redis(self, redis_mock):
        # The helper resolves get_redis_client from app.core.redis module
        # globals at call time, so patching the module attribute is enough.
        return patch("app.core.redis.get_redis_client", new=AsyncMock(return_value=redis_mock))

    async def test_over_limit_then_recover_after_key_expiry(self, redis_mock):
        # Rejected requests are recorded too: while the attack continues the
        # window stays full. Only once the key's TTL lapses (window + buffer)
        # does capacity return — here driven through the virtual clock.
        middleware = RateLimitMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        with (
            self._patch_helper_redis(redis_mock),
            patch.object(settings, "rate_limit_per_minute", 2),
            patch.object(settings, "rate_limit_burst", 0),
        ):
            assert await middleware.dispatch(_request(), call_next) is response
            assert await middleware.dispatch(_request(), call_next) is response
            first_429 = await middleware.dispatch(_request(), call_next)
            assert first_429.status_code == 429

            # TTL is window(60) + buffer(5): advancing the MockRedis clock to
            # 66s purges the key, so the next request starts a fresh window.
            redis_mock.advance(66)
            assert await middleware.dispatch(_request(), call_next) is response

    async def test_window_slides_by_score_trimming(self, redis_mock):
        # Same scenario, but recovery comes from ZREMRANGEBYSCORE trimming
        # inside the window (virtual clock frozen, so the key TTL never
        # lapses): members older than 60s slide out of the count.
        clock = _FakeClock()
        middleware = RateLimitMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        with (
            self._patch_helper_redis(redis_mock),
            patch("app.core.middleware.time.time", new=clock),
            patch.object(settings, "rate_limit_per_minute", 2),
            patch.object(settings, "rate_limit_burst", 0),
        ):
            assert await middleware.dispatch(_request(), call_next) is response
            assert await middleware.dispatch(_request(), call_next) is response
            assert (await middleware.dispatch(_request(), call_next)).status_code == 429

            # 30s later every member is still inside the window: still 429
            # (and the rejected request keeps consuming budget).
            clock.advance(30)
            assert (await middleware.dispatch(_request(), call_next)).status_code == 429

            # 91s from the start: everything recorded before t+31s slid out.
            clock.advance(61)
            assert await middleware.dispatch(_request(), call_next) is response

    async def test_tenant_buckets_are_independent(self, redis_mock):
        middleware = RateLimitMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        request_a = _request(headers={"authorization": f"Bearer {_token(TENANT_A)}"})
        request_b = _request(headers={"authorization": f"Bearer {_token(TENANT_B)}"})
        with (
            self._patch_helper_redis(redis_mock),
            patch.object(settings, "rate_limit_per_minute", 1),
            patch.object(settings, "rate_limit_burst", 0),
        ):
            assert await middleware.dispatch(request_a, call_next) is response
            assert (await middleware.dispatch(request_a, call_next)).status_code == 429
            # Tenant B shares the IP but has its own bucket: unaffected.
            assert await middleware.dispatch(request_b, call_next) is response
            assert (await middleware.dispatch(request_b, call_next)).status_code == 429

        assert f"rate:{TENANT_A}:{DEFAULT_IP}:default" in redis_mock._data
        assert f"rate:{TENANT_B}:{DEFAULT_IP}:default" in redis_mock._data
