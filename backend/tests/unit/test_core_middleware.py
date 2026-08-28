from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.middleware import (
    MILESTONE_LOG_EVERY,
    SLOW_REQUEST_THRESHOLD_MS,
    OriginGuardMiddleware,
    RequestLoggingMiddleware,
    RequestValidationMiddleware,
    _record_request_stats,
    _status_bucket,
    _total_request_count,
    setup_cors,
    setup_middlewares,
)
from app.core.redis import RedisKeys

# Frozen clock: 1700000000 // 60 = 28333333, so the minute key the writer is
# expected to touch is dashboard:api_metrics:minute:28333333.
FROZEN_NOW = 1700000000
FROZEN_MINUTE = FROZEN_NOW // 60


def _mock_redis_pipeline():
    pipe = MagicMock()
    pipe.hincrby = MagicMock()
    pipe.hincrbyfloat = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1, True, 1, True])
    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)
    return client, pipe


def _pipeline_calls(pipe, name):
    return [call.args for call in getattr(pipe, name).call_args_list]


class TestSetupCors:
    def test_calls_add_middleware(self):
        app = MagicMock()
        setup_cors(app)
        app.add_middleware.assert_called_once()
        call_args = app.add_middleware.call_args
        from fastapi.middleware.cors import CORSMiddleware

        assert call_args[0][0] == CORSMiddleware
        assert call_args[1]["allow_credentials"] is True
        assert "GET" in call_args[1]["allow_methods"]
        assert "Authorization" in call_args[1]["allow_headers"]


class TestStatusBucket:
    @pytest.mark.parametrize(
        "status_code,expected",
        [
            (200, "count_2xx"),
            (204, "count_2xx"),
            (404, "count_4xx"),
            (429, "count_4xx"),
            (500, "count_5xx"),
            (503, "count_5xx"),
            (101, None),
            (301, None),
            (302, None),
        ],
    )
    def test_bucket_mapping(self, status_code, expected):
        assert _status_bucket(status_code) == expected


class TestRecordRequestStats:
    async def test_writes_totals_minute_buckets_in_one_pipeline(self):
        client, pipe = _mock_redis_pipeline()

        with (
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
            patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
        ):
            mock_client.return_value = client
            await _record_request_stats(123.456, 404)

        minute_key = RedisKeys.api_metrics_minute_key(FROZEN_MINUTE)
        assert pipe.hincrby.call_args_list[0].args == (RedisKeys.API_METRICS_TOTALS, "requests_total", 1)
        assert _pipeline_calls(pipe, "hincrby") == [
            (RedisKeys.API_METRICS_TOTALS, "requests_total", 1),
            (minute_key, "count", 1),
            (minute_key, "count_4xx", 1),
        ]
        assert _pipeline_calls(pipe, "hincrbyfloat") == [(minute_key, "latency_ms", 123.456)]
        assert _pipeline_calls(pipe, "expire") == [(minute_key, RedisKeys.API_METRICS_MINUTE_TTL)]
        pipe.execute.assert_awaited_once()

    async def test_2xx_and_5xx_bucket_fields(self):
        for status_code, expected_field in [(200, "count_2xx"), (503, "count_5xx")]:
            client, pipe = _mock_redis_pipeline()
            with (
                patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
                patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
            ):
                mock_client.return_value = client
                await _record_request_stats(10.0, status_code)

            minute_key = RedisKeys.api_metrics_minute_key(FROZEN_MINUTE)
            assert _pipeline_calls(pipe, "hincrby") == [
                (RedisKeys.API_METRICS_TOTALS, "requests_total", 1),
                (minute_key, "count", 1),
                (minute_key, expected_field, 1),
            ]

    async def test_3xx_has_no_status_bucket(self):
        client, pipe = _mock_redis_pipeline()
        with (
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
            patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
        ):
            mock_client.return_value = client
            await _record_request_stats(10.0, 302)

        minute_key = RedisKeys.api_metrics_minute_key(FROZEN_MINUTE)
        assert _pipeline_calls(pipe, "hincrby") == [
            (RedisKeys.API_METRICS_TOTALS, "requests_total", 1),
            (minute_key, "count", 1),
        ]

    async def test_client_failure_is_swallowed(self):
        with patch("app.core.middleware.get_redis_client", new_callable=AsyncMock, side_effect=Exception("Redis down")):
            await _record_request_stats(10.0, 200)

    async def test_pipeline_failure_is_swallowed(self):
        client, pipe = _mock_redis_pipeline()
        pipe.execute = AsyncMock(side_effect=Exception("boom"))
        with (
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
            patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
        ):
            mock_client.return_value = client
            await _record_request_stats(10.0, 200)


class TestRequestLoggingMiddleware:
    def test_skip_paths(self):
        assert "/api/v1/health" in RequestLoggingMiddleware.SKIP_PATHS
        assert "/api/v1/stream" in RequestLoggingMiddleware.SKIP_PATHS

    async def test_dispatch_records_stats(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/categories"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.1]),
            patch("app.core.middleware._record_request_stats", new_callable=AsyncMock) as mock_record,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response
        call_next.assert_called_once()
        mock_record.assert_awaited_once()
        duration_ms, status_code = mock_record.call_args.args
        assert duration_ms == pytest.approx(100.0)
        assert status_code == 200

    async def test_dispatch_skip_path_does_not_record(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/health"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.1]),
            patch("app.core.middleware._record_request_stats", new_callable=AsyncMock) as mock_record,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response
        mock_record.assert_not_awaited()

    async def test_dispatch_slow_request_does_not_record(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, SLOW_REQUEST_THRESHOLD_MS / 1000 + 1]),
            patch("app.core.middleware._record_request_stats", new_callable=AsyncMock) as mock_record,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response
        mock_record.assert_not_awaited()

    async def test_dispatch_redis_failure_does_not_affect_response(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock, side_effect=Exception("Redis down")),
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response

    async def test_dispatch_milestone_logging(self, caplog):
        import app.core.middleware as mw

        middleware = RequestLoggingMiddleware(app=MagicMock())
        old_count = mw._total_request_count
        mw._total_request_count = MILESTONE_LOG_EVERY - 1

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        try:
            with (
                patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
                patch("app.core.middleware._record_request_stats", new_callable=AsyncMock),
                caplog.at_level("INFO", logger="instantboard"),
            ):
                result = await middleware.dispatch(request, call_next)

            assert result == response
            assert mw._total_request_count == MILESTONE_LOG_EVERY
            assert any("milestone" in record.message for record in caplog.records)
        finally:
            mw._total_request_count = old_count


class TestSetupMiddlewares:
    def test_calls_setup_cors_and_adds_logging_middleware(self):
        app = MagicMock()
        setup_middlewares(app)
        call_list = [call[0][0].__name__ for call in app.add_middleware.call_args_list]
        # Registration order: CORS -> RequestValidation -> OriginGuard ->
        # RateLimit -> RequestLogging -> IPBlacklist. add_middleware prepends,
        # so the runtime stack order is IPBlacklist -> RequestLogging ->
        # RateLimit -> OriginGuard -> RequestValidation -> CORS -> app: banned
        # IPs are dropped outermost (before rate limiting and request logging),
        # while rate-limited 429s still land inside RequestLogging and are
        # counted in the request statistics. Rate limit behavior is covered by
        # tests/unit/test_rate_limit.py.
        assert call_list == [
            "CORSMiddleware",
            "RequestValidationMiddleware",
            "OriginGuardMiddleware",
            "RateLimitMiddleware",
            "RequestLoggingMiddleware",
            "IPBlacklistMiddleware",
        ]


class TestModuleConstants:
    def test_total_request_count_is_int(self):
        assert isinstance(_total_request_count, int)

    def test_milestone_interval(self):
        assert MILESTONE_LOG_EVERY == 1000


ALLOWED_ORIGINS = ["http://localhost:3000", "https://ib.example.com:8443"]


def _origin_guard_request(method="POST", headers=None):
    request = MagicMock()
    request.method = method
    request.headers = headers or {}
    request.url.path = "/api/v1/categories"
    return request


async def _dispatch_guard(method="POST", headers=None, allowed=ALLOWED_ORIGINS):
    middleware = OriginGuardMiddleware(app=MagicMock())
    response = MagicMock()
    call_next = AsyncMock(return_value=response)
    with patch("app.core.middleware.settings.cors_origins", allowed):
        result = await middleware.dispatch(_origin_guard_request(method, headers), call_next)
    return result, response, call_next


def _assert_forbidden_envelope(result):
    assert result.status_code == 403
    assert result.body is not None
    import json

    payload = json.loads(result.body)
    assert payload == {
        "success": False,
        "error": {"code": "FORBIDDEN", "message": "Origin not allowed", "details": None},
    }


class TestOriginGuardMiddleware:
    async def test_allowed_origin_passes(self):
        result, response, call_next = await _dispatch_guard(headers={"origin": "http://localhost:3000"})
        assert result == response
        call_next.assert_called_once()

    async def test_disallowed_origin_returns_403_envelope(self):
        result, _response, call_next = await _dispatch_guard(headers={"origin": "https://evil.example"})
        _assert_forbidden_envelope(result)
        call_next.assert_not_called()

    async def test_origin_port_must_match_exactly(self):
        result, _response, call_next = await _dispatch_guard(headers={"origin": "http://localhost:3001"})
        _assert_forbidden_envelope(result)
        call_next.assert_not_called()

    async def test_allowed_origin_with_port_passes(self):
        result, response, call_next = await _dispatch_guard(headers={"origin": "https://ib.example.com:8443"})
        assert result == response
        call_next.assert_called_once()

    async def test_no_origin_with_allowed_referer_passes(self):
        result, response, call_next = await _dispatch_guard(headers={"referer": "http://localhost:3000/board/new?x=1"})
        assert result == response
        call_next.assert_called_once()

    async def test_no_origin_with_disallowed_referer_returns_403(self):
        result, _response, call_next = await _dispatch_guard(headers={"referer": "https://evil.example/steal"})
        _assert_forbidden_envelope(result)
        call_next.assert_not_called()

    async def test_referer_port_comparison(self):
        result, response, call_next = await _dispatch_guard(headers={"referer": "https://ib.example.com:8443/x"})
        assert result == response
        call_next.assert_called_once()

        result, _response, call_next = await _dispatch_guard(headers={"referer": "https://ib.example.com/x"})
        _assert_forbidden_envelope(result)
        call_next.assert_not_called()

    async def test_unparseable_referer_is_allowed(self):
        result, response, call_next = await _dispatch_guard(headers={"referer": "not-a-url"})
        assert result == response
        call_next.assert_called_once()

    async def test_no_origin_and_no_referer_passes(self):
        result, response, call_next = await _dispatch_guard(headers={})
        assert result == response
        call_next.assert_called_once()

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    async def test_state_changing_methods_are_checked(self, method):
        result, _response, call_next = await _dispatch_guard(method=method, headers={"origin": "https://evil.example"})
        _assert_forbidden_envelope(result)
        call_next.assert_not_called()

    @pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
    async def test_safe_methods_are_not_checked(self, method):
        result, response, call_next = await _dispatch_guard(method=method, headers={"origin": "https://evil.example"})
        assert result == response
        call_next.assert_called_once()

    async def test_wildcard_allows_any_origin(self):
        result, response, call_next = await _dispatch_guard(
            headers={"origin": "https://anything.example"}, allowed=["*"]
        )
        assert result == response
        call_next.assert_called_once()

    async def test_parsing_failure_fails_open(self):
        middleware = OriginGuardMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        with (
            patch("app.core.middleware.settings.cors_origins", ALLOWED_ORIGINS),
            patch("app.core.middleware.urlparse", side_effect=ValueError("boom")),
        ):
            result = await middleware.dispatch(
                _origin_guard_request(headers={"referer": "https://evil.example/"}), call_next
            )
        assert result == response
        call_next.assert_called_once()

    async def test_parsing_failure_fails_open_logs_warning(self, caplog):
        middleware = OriginGuardMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock())
        with (
            patch("app.core.middleware.settings.cors_origins", ALLOWED_ORIGINS),
            patch("app.core.middleware.urlparse", side_effect=ValueError("boom")),
            caplog.at_level("WARNING", logger="instantboard"),
        ):
            await middleware.dispatch(_origin_guard_request(headers={"referer": "https://evil.example/"}), call_next)
        assert any("Origin guard check failed" in record.message for record in caplog.records)


UA_HEADER = {"user-agent": "instantboard-test/1.0"}

DEFAULT_BODY_LIMIT = 10 * 1024


def _validation_request(path="/api/v1/categories", headers=None):
    request = MagicMock()
    request.method = "POST"
    request.url.path = path
    request.headers = headers if headers is not None else {}
    return request


async def _dispatch_validation(path="/api/v1/categories", headers=None, require_ua=True, max_bytes=DEFAULT_BODY_LIMIT):
    middleware = RequestValidationMiddleware(app=MagicMock())
    response = MagicMock()
    call_next = AsyncMock(return_value=response)
    with (
        patch("app.core.middleware.settings.require_user_agent", require_ua),
        patch("app.core.middleware.settings.max_request_body_bytes", max_bytes),
    ):
        result = await middleware.dispatch(_validation_request(path, headers), call_next)
    return result, response, call_next


def _assert_envelope(result, status_code, message):
    assert result.status_code == status_code
    import json

    payload = json.loads(result.body)
    assert payload == {
        "success": False,
        "error": {"code": "VALIDATION_ERROR", "message": message, "details": None},
    }


def _assert_ua_rejected(result):
    _assert_envelope(result, 400, "User-Agent header is required")


def _assert_body_rejected(result, limit=DEFAULT_BODY_LIMIT):
    _assert_envelope(result, 413, f"Request body must not exceed {limit} bytes")


class TestRequestValidationUserAgent:
    async def test_missing_user_agent_is_rejected(self):
        result, _response, call_next = await _dispatch_validation(headers={})
        _assert_ua_rejected(result)
        call_next.assert_not_called()

    @pytest.mark.parametrize("user_agent", ["", "   ", "\t"])
    async def test_blank_user_agent_is_rejected(self, user_agent):
        result, _response, call_next = await _dispatch_validation(headers={"user-agent": user_agent})
        _assert_ua_rejected(result)
        call_next.assert_not_called()

    async def test_valid_user_agent_passes(self):
        result, response, call_next = await _dispatch_validation(headers=UA_HEADER)
        assert result == response
        call_next.assert_called_once()

    async def test_disabled_switch_lets_missing_ua_pass(self):
        result, response, call_next = await _dispatch_validation(headers={}, require_ua=False)
        assert result == response
        call_next.assert_called_once()

    async def test_health_path_is_exempt(self):
        result, response, call_next = await _dispatch_validation(path="/api/v1/health", headers={})
        assert result == response
        call_next.assert_called_once()

    async def test_health_detail_path_is_exempt(self):
        result, response, call_next = await _dispatch_validation(path="/api/v1/health/detail", headers={})
        assert result == response
        call_next.assert_called_once()

    @pytest.mark.parametrize("path", ["/healthz", "/", "/api/docs"])
    async def test_non_api_paths_are_untouched(self, path):
        result, response, call_next = await _dispatch_validation(path=path, headers={})
        assert result == response
        call_next.assert_called_once()

    async def test_ua_checked_for_get_without_body(self):
        result, _response, call_next = await _dispatch_validation(path="/api/v1/categories", headers={})
        _assert_ua_rejected(result)
        call_next.assert_not_called()


class TestRequestValidationBodySize:
    async def test_oversized_content_length_is_rejected(self):
        headers = {**UA_HEADER, "content-length": str(DEFAULT_BODY_LIMIT + 1)}
        result, _response, call_next = await _dispatch_validation(headers=headers)
        _assert_body_rejected(result)
        call_next.assert_not_called()

    async def test_content_length_at_the_limit_passes(self):
        headers = {**UA_HEADER, "content-length": str(DEFAULT_BODY_LIMIT)}
        result, response, call_next = await _dispatch_validation(headers=headers)
        assert result == response
        call_next.assert_called_once()

    async def test_content_length_below_the_limit_passes(self):
        headers = {**UA_HEADER, "content-length": "123"}
        result, response, call_next = await _dispatch_validation(headers=headers)
        assert result == response
        call_next.assert_called_once()

    async def test_no_content_length_is_not_limited(self):
        # Chunked/streamed requests carry no Content-Length and are intentionally
        # passed through (this layer is a cheap pre-filter, not a byte limit).
        result, response, call_next = await _dispatch_validation(headers=UA_HEADER)
        assert result == response
        call_next.assert_called_once()

    async def test_custom_limit_is_respected(self):
        headers = {**UA_HEADER, "content-length": "2049"}
        result, _response, call_next = await _dispatch_validation(headers=headers, max_bytes=2048)
        _assert_body_rejected(result, limit=2048)
        call_next.assert_not_called()

    async def test_missing_ua_takes_precedence_over_body_size(self):
        headers = {"content-length": str(DEFAULT_BODY_LIMIT + 1)}
        result, _response, call_next = await _dispatch_validation(headers=headers)
        _assert_ua_rejected(result)
        call_next.assert_not_called()

    async def test_invalid_content_length_fails_open(self):
        headers = {**UA_HEADER, "content-length": "not-a-number"}
        result, response, call_next = await _dispatch_validation(headers=headers)
        assert result == response
        call_next.assert_called_once()

    async def test_invalid_content_length_fails_open_logs_warning(self, caplog):
        headers = {**UA_HEADER, "content-length": "not-a-number"}
        middleware = RequestValidationMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock())
        with (
            patch("app.core.middleware.settings.require_user_agent", True),
            patch("app.core.middleware.settings.max_request_body_bytes", DEFAULT_BODY_LIMIT),
            caplog.at_level("WARNING", logger="instantboard"),
        ):
            await middleware.dispatch(_validation_request(headers=headers), call_next)
        assert any("Request validation check failed" in record.message for record in caplog.records)
