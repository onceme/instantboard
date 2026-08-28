"""Unit tests for the layer-3 IP blacklist (security.md §3.3):
IPBlacklistMiddleware (blocking, XFF rightmost rule, cache, fail-open) and the
admin management handlers in app/api/v1/admin.py.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.core.middleware as middleware_mod
from app.core.exceptions import Forbidden, ValidationError
from app.core.middleware import (
    IPBlacklistMiddleware,
    invalidate_ipblacklist_cache,
    setup_middlewares,
)
from app.core.redis import RedisKeys

BANNED_IP = "203.0.113.7"  # TEST-NET-3, guaranteed unroutable
OTHER_IP = "198.51.100.23"


@pytest.fixture(autouse=True)
def _clean_blacklist_cache():
    """The snapshot cache is module-global; tests must never see each other's state."""
    invalidate_ipblacklist_cache()
    yield
    invalidate_ipblacklist_cache()


def _request(client_ip: str = "127.0.0.1", xff: str | None = None) -> MagicMock:
    request = MagicMock()
    request.headers = {"x-forwarded-for": xff} if xff else {}
    request.client.host = client_ip
    return request


def _patch_redis(members=None, side_effect=None):
    """Patch the redis client the middleware resolves; returns the smembers mock."""
    smembers = AsyncMock(return_value=set(members or set()) if side_effect is None else None)
    if side_effect is not None:
        smembers = AsyncMock(side_effect=side_effect)
    client = MagicMock()
    client.smembers = smembers
    patcher = patch("app.core.middleware.get_redis_client", new=AsyncMock(return_value=client))
    return patcher, smembers


def _forbidden_envelope():
    return {
        "success": False,
        "error": {"code": "FORBIDDEN", "message": "Access denied", "details": None},
    }


async def _dispatch(request, members=None, side_effect=None):
    middleware = IPBlacklistMiddleware(app=MagicMock())
    response = MagicMock()
    call_next = AsyncMock(return_value=response)
    patcher, smembers = _patch_redis(members, side_effect)
    with patcher:
        result = await middleware.dispatch(request, call_next)
    return result, response, call_next, smembers


class TestIPBlacklistMiddlewareBlocking:
    async def test_banned_direct_ip_is_rejected(self):
        result, _resp, call_next, _sm = await _dispatch(_request(client_ip=BANNED_IP), members={BANNED_IP})
        assert result.status_code == 403
        assert json.loads(result.body) == _forbidden_envelope()
        call_next.assert_not_called()

    async def test_unbanned_ip_passes(self):
        result, response, call_next, _sm = await _dispatch(_request(client_ip=OTHER_IP), members={BANNED_IP})
        assert result is response
        call_next.assert_awaited_once()

    async def test_empty_blacklist_passes_everyone(self):
        result, response, call_next, _sm = await _dispatch(_request(client_ip=BANNED_IP), members=set())
        assert result is response
        call_next.assert_awaited_once()

    @pytest.mark.parametrize("local_ip", ["127.0.0.1", "::1"])
    async def test_local_addresses_not_collateral_damage(self, local_ip):
        # Banning some external IP must never block loopback traffic (dev/test safety).
        result, response, call_next, _sm = await _dispatch(_request(client_ip=local_ip), members={BANNED_IP})
        assert result is response
        call_next.assert_awaited_once()

    async def test_empty_blacklist_skips_client_ip_extraction(self):
        with patch("app.core.middleware.get_client_ip") as mock_get_ip:
            _result, _resp, _call_next, _sm = await _dispatch(_request(client_ip=BANNED_IP), members=set())
        mock_get_ip.assert_not_called()


class TestIPBlacklistMiddlewareXFF:
    async def test_rightmost_hop_decides_block(self):
        # Genuine peer (added by the trusted proxy) is the rightmost hop.
        request = _request(client_ip="127.0.0.1", xff=f"10.0.0.1, {BANNED_IP}")
        result, _resp, call_next, _sm = await _dispatch(request, members={BANNED_IP})
        assert result.status_code == 403
        call_next.assert_not_called()

    async def test_forged_left_hop_does_not_bypass(self):
        # Attacker prepends a banned-looking or arbitrary left hop; the rightmost
        # (proxy-added) address is the one that counts.
        request = _request(client_ip=OTHER_IP, xff=f"{BANNED_IP}, spoofed.example")
        result, response, call_next, _sm = await _dispatch(request, members={BANNED_IP})
        # Rightmost well-formed hop is OTHER_IP? No — "spoofed.example" is not an
        # IP, so the walk lands on BANNED_IP and the request is blocked.
        assert result.status_code == 403
        call_next.assert_not_called()

    async def test_forged_left_hop_does_not_frame_victim(self):
        # A banned IP forged on the LEFT must not block the genuine rightmost peer.
        request = _request(client_ip="127.0.0.1", xff=f"{BANNED_IP}, {OTHER_IP}")
        result, response, call_next, _sm = await _dispatch(request, members={BANNED_IP})
        assert result is response
        call_next.assert_awaited_once()

    async def test_xff_takes_precedence_over_socket_peer(self):
        request = _request(client_ip=BANNED_IP, xff=OTHER_IP)
        result, response, call_next, _sm = await _dispatch(request, members={BANNED_IP})
        assert result is response
        call_next.assert_awaited_once()


class TestIPBlacklistCache:
    async def test_second_request_served_from_cache(self):
        middleware = IPBlacklistMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock())
        patcher, smembers = _patch_redis(members={BANNED_IP})
        with patcher:
            first = await middleware.dispatch(_request(client_ip=OTHER_IP), call_next)
            assert first is call_next.return_value
            # The ban decision comes from the cached snapshot: no second SMEMBERS.
            second = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert second.status_code == 403
        smembers.assert_awaited_once()

    async def test_expired_cache_is_refreshed(self, monkeypatch):
        monkeypatch.setattr(middleware_mod.settings, "ip_blacklist_cache_ttl", 0)
        middleware = IPBlacklistMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock())
        patcher, smembers = _patch_redis(side_effect=[{BANNED_IP}, set()])
        with patcher:
            first = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert first.status_code == 403
            # TTL lapsed immediately: the fresh (now empty) snapshot governs.
            second = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert second is call_next.return_value
        assert smembers.await_count == 2

    async def test_invalidate_forces_immediate_refetch(self):
        middleware = IPBlacklistMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        patcher, smembers = _patch_redis(side_effect=[set(), {BANNED_IP}])
        with patcher:
            first = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert first is response
            invalidate_ipblacklist_cache()
            second = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert second.status_code == 403
        assert smembers.await_count == 2


class TestIPBlacklistFailOpen:
    async def test_redis_failure_passes_request(self):
        result, response, call_next, _sm = await _dispatch(
            _request(client_ip=BANNED_IP), side_effect=ConnectionError("redis down")
        )
        assert result is response
        call_next.assert_awaited_once()

    async def test_redis_failure_logs_debug(self, caplog):
        import logging

        middleware = IPBlacklistMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock())
        patcher, _sm = _patch_redis(side_effect=ConnectionError("redis down"))
        with patcher, caplog.at_level(logging.DEBUG, logger="instantboard"):
            await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
        assert any("failing open" in record.message for record in caplog.records)
        call_next.assert_awaited_once()

    async def test_smembers_exception_fails_open_and_cache_stays_empty(self):
        # After a failed fetch the next request retries (no poisoned cache).
        middleware = IPBlacklistMiddleware(app=MagicMock())
        response = MagicMock()
        call_next = AsyncMock(return_value=response)
        patcher, smembers = _patch_redis(side_effect=[ConnectionError("down"), {BANNED_IP}])
        with patcher:
            first = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert first is response
            second = await middleware.dispatch(_request(client_ip=BANNED_IP), call_next)
            assert second.status_code == 403
        assert smembers.await_count == 2


class TestSetupMiddlewaresBlacklistOutermost:
    def test_blacklist_registered_last_thus_outermost(self):
        app = MagicMock()
        setup_middlewares(app)
        call_list = [call[0][0].__name__ for call in app.add_middleware.call_args_list]
        # add_middleware prepends: the LAST registration runs FIRST at request
        # time, so banned IPs are dropped before logging/validation/origin
        # checks and never consume rate-limit budget.
        assert call_list[-1] == "IPBlacklistMiddleware"
        assert call_list == [
            "CORSMiddleware",
            "RequestValidationMiddleware",
            "OriginGuardMiddleware",
            "RateLimitMiddleware",
            "RequestLoggingMiddleware",
            "IPBlacklistMiddleware",
        ]


# ---------------------------------------------------------------------------
# Admin management handlers (direct calls, HTTP stack covered by integration)
# ---------------------------------------------------------------------------

from app.api.v1.admin import (  # noqa: E402
    _parse_blacklist_ip,
    add_to_ip_blacklist,
    list_ip_blacklist,
    remove_from_ip_blacklist,
)
from app.schemas.admin import IPBlacklistAddRequest  # noqa: E402

ADMIN_USER = {"role": "admin", "user_id": "u1"}
MEMBER_USER = {"role": "member", "user_id": "u2"}


class TestParseBlacklistIp:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("192.0.2.1", "192.0.2.1"),
            ("  192.0.2.1  ", "192.0.2.1"),
            ("::1", "::1"),
            ("0:0:0:0:0:0:0:1", "::1"),  # IPv6 normalization
            ("2001:0DB8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ],
    )
    def test_valid_ips_canonicalized(self, raw, expected):
        assert _parse_blacklist_ip(raw) == expected

    @pytest.mark.parametrize("raw", ["999.1.1.1", "not-an-ip", "", " ", "1.2.3.4/24", "::g", "1.2.3", "0xc0.1.1.1"])
    def test_invalid_ips_raise_validation_error(self, raw):
        with pytest.raises(ValidationError) as exc_info:
            _parse_blacklist_ip(raw)
        assert exc_info.value.status_code == 400


class TestAddToBlacklist:
    async def test_add_succeeds_and_invalidates_cache(self):
        with (
            patch("app.api.v1.admin.redis_sadd", new_callable=AsyncMock, return_value=1) as mock_sadd,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache") as mock_invalidate,
        ):
            resp = await add_to_ip_blacklist(IPBlacklistAddRequest(ip=BANNED_IP), user=ADMIN_USER)
        assert resp.success is True
        assert resp.data.ip == BANNED_IP
        mock_sadd.assert_awaited_once_with(RedisKeys.IP_BLACKLIST, BANNED_IP)
        mock_invalidate.assert_called_once()

    async def test_duplicate_add_is_idempotent(self):
        with (
            patch("app.api.v1.admin.redis_sadd", new_callable=AsyncMock, return_value=0) as mock_sadd,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache"),
        ):
            resp = await add_to_ip_blacklist(IPBlacklistAddRequest(ip=BANNED_IP), user=ADMIN_USER)
        assert resp.success is True
        assert resp.data.ip == BANNED_IP
        mock_sadd.assert_awaited_once()

    async def test_ipv6_stored_canonical(self):
        with (
            patch("app.api.v1.admin.redis_sadd", new_callable=AsyncMock) as mock_sadd,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache"),
        ):
            resp = await add_to_ip_blacklist(IPBlacklistAddRequest(ip="0:0:0:0:0:0:0:1"), user=ADMIN_USER)
        assert resp.data.ip == "::1"
        mock_sadd.assert_awaited_once_with(RedisKeys.IP_BLACKLIST, "::1")

    @pytest.mark.parametrize("raw", ["not-an-ip", "999.999.999.999", ""])
    async def test_invalid_ip_raises_400(self, raw):
        with (
            patch("app.api.v1.admin.redis_sadd", new_callable=AsyncMock) as mock_sadd,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache"),
            pytest.raises(ValidationError),
        ):
            await add_to_ip_blacklist(IPBlacklistAddRequest(ip=raw), user=ADMIN_USER)
        mock_sadd.assert_not_awaited()

    async def test_non_admin_forbidden(self):
        with (
            patch("app.api.v1.admin.redis_sadd", new_callable=AsyncMock) as mock_sadd,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache"),
            pytest.raises(Forbidden),
        ):
            await add_to_ip_blacklist(IPBlacklistAddRequest(ip=BANNED_IP), user=MEMBER_USER)
        mock_sadd.assert_not_awaited()


class TestListBlacklist:
    async def test_list_sorted(self):
        with patch("app.api.v1.admin.redis_smembers", new_callable=AsyncMock, return_value={"9.9.9.9", "1.1.1.1"}):
            resp = await list_ip_blacklist(user=ADMIN_USER)
        assert resp.data.ips == ["1.1.1.1", "9.9.9.9"]

    async def test_list_empty(self):
        with patch("app.api.v1.admin.redis_smembers", new_callable=AsyncMock, return_value=set()):
            resp = await list_ip_blacklist(user=ADMIN_USER)
        assert resp.data.ips == []

    async def test_non_admin_forbidden(self):
        with pytest.raises(Forbidden):
            await list_ip_blacklist(user=MEMBER_USER)


class TestRemoveFromBlacklist:
    async def test_remove_succeeds_and_invalidates_cache(self):
        with (
            patch("app.api.v1.admin.redis_srem", new_callable=AsyncMock, return_value=1) as mock_srem,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache") as mock_invalidate,
        ):
            resp = await remove_from_ip_blacklist(BANNED_IP, user=ADMIN_USER)
        assert resp.success is True
        assert resp.data.ip == BANNED_IP
        mock_srem.assert_awaited_once_with(RedisKeys.IP_BLACKLIST, BANNED_IP)
        mock_invalidate.assert_called_once()

    async def test_remove_missing_is_idempotent_success(self):
        # Chosen semantics: DELETE succeeds even when the IP was not banned
        # ("ensure this IP is not banned"); see handler comment.
        with (
            patch("app.api.v1.admin.redis_srem", new_callable=AsyncMock, return_value=0),
            patch("app.api.v1.admin.invalidate_ipblacklist_cache"),
        ):
            resp = await remove_from_ip_blacklist(OTHER_IP, user=ADMIN_USER)
        assert resp.success is True
        assert resp.data.ip == OTHER_IP

    async def test_remove_normalizes_ipv6_path_param(self):
        with (
            patch("app.api.v1.admin.redis_srem", new_callable=AsyncMock, return_value=1) as mock_srem,
            patch("app.api.v1.admin.invalidate_ipblacklist_cache"),
        ):
            await remove_from_ip_blacklist("0:0:0:0:0:0:0:1", user=ADMIN_USER)
        mock_srem.assert_awaited_once_with(RedisKeys.IP_BLACKLIST, "::1")

    async def test_invalid_ip_raises_400(self):
        with (
            patch("app.api.v1.admin.redis_srem", new_callable=AsyncMock) as mock_srem,
            pytest.raises(ValidationError),
        ):
            await remove_from_ip_blacklist("not-an-ip", user=ADMIN_USER)
        mock_srem.assert_not_awaited()

    async def test_non_admin_forbidden(self):
        with (
            patch("app.api.v1.admin.redis_srem", new_callable=AsyncMock) as mock_srem,
            pytest.raises(Forbidden),
        ):
            await remove_from_ip_blacklist(BANNED_IP, user=MEMBER_USER)
        mock_srem.assert_not_awaited()
