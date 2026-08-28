"""Integration tests for the layer-3 IP blacklist (security.md §3.3):
admin management endpoints through the full HTTP stack (auth + envelope), and
IPBlacklistMiddleware enforcement through the real middleware chain.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.core.middleware import invalidate_ipblacklist_cache
from app.core.redis import RedisKeys
from tests.integration.conftest import make_admin_headers, make_user_headers

# TEST-NET-3 / TEST-NET-2 ranges (RFC 5737) — guaranteed never routed anywhere.
BANNED_IP = "203.0.113.7"
SECOND_IP = "198.51.100.23"
BASE = "/api/v1/admin/security/ip-blacklist"

FORBIDDEN_ENVELOPE = {
    "success": False,
    "error": {"code": "FORBIDDEN", "message": "Access denied", "details": None},
}


@pytest.fixture
def blacklist(client, app_with_overrides):
    """Fresh blacklist state per test: empty Redis set + empty middleware cache."""
    _app, mock_redis = app_with_overrides
    mock_redis._data.pop(RedisKeys.IP_BLACKLIST, None)
    invalidate_ipblacklist_cache()
    yield mock_redis
    mock_redis._data.pop(RedisKeys.IP_BLACKLIST, None)
    invalidate_ipblacklist_cache()


def _ban(client, ip, headers=None):
    if headers is None:
        headers, _t, _u = make_admin_headers()
    return client.post(BASE, headers=headers, json={"ip": ip})


class TestBlacklistAdminEndpoints:
    def test_add_list_delete_round_trip(self, client, blacklist):
        headers, _tenant, _user = make_admin_headers()

        resp = _ban(client, BANNED_IP, headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {"ip": BANNED_IP}
        assert BANNED_IP in blacklist._data[RedisKeys.IP_BLACKLIST]

        resp = client.get(BASE, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["ips"] == [BANNED_IP]

        resp = client.delete(f"{BASE}/{BANNED_IP}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == {"ip": BANNED_IP}

        resp = client.get(BASE, headers=headers)
        assert resp.json()["data"]["ips"] == []

    def test_list_is_sorted(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        _ban(client, SECOND_IP, headers)
        _ban(client, BANNED_IP, headers)
        resp = client.get(BASE, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["ips"] == sorted([BANNED_IP, SECOND_IP])

    def test_duplicate_add_is_idempotent(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        first = _ban(client, BANNED_IP, headers)
        second = _ban(client, BANNED_IP, headers)
        assert first.status_code == 200
        assert second.status_code == 200
        resp = client.get(BASE, headers=headers)
        assert resp.json()["data"]["ips"] == [BANNED_IP]

    def test_add_ipv6_is_normalized(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        resp = _ban(client, "0:0:0:0:0:0:0:1", headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == {"ip": "::1"}
        resp = client.get(BASE, headers=headers)
        assert resp.json()["data"]["ips"] == ["::1"]

    @pytest.mark.parametrize("bad_ip", ["not-an-ip", "999.1.2.3", "1.2.3.4/24", ""])
    def test_add_invalid_ip_returns_400(self, client, blacklist, bad_ip):
        headers, _t, _u = make_admin_headers()
        resp = _ban(client, bad_ip, headers)
        assert resp.status_code == 400
        # Handler-raised AppExceptions pass FastAPI's HTTPException handler,
        # which nests the envelope under "detail" (cf. test_api_auth.py).
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"
        assert resp.json()["detail"]["error"]["message"]
        assert RedisKeys.IP_BLACKLIST not in blacklist._data

    def test_add_missing_ip_field_returns_422(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        resp = client.post(BASE, headers=headers, json={})
        assert resp.status_code == 422

    def test_delete_missing_ip_is_idempotent_success(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        resp = client.delete(f"{BASE}/{SECOND_IP}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == {"ip": SECOND_IP}

    def test_delete_invalid_ip_returns_400(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        resp = client.delete(f"{BASE}/not-an-ip", headers=headers)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    @pytest.mark.parametrize("method", ["get", "post", "delete"])
    def test_non_admin_is_forbidden(self, client, blacklist, method):
        headers, _t, _u = make_user_headers()
        if method == "get":
            resp = client.get(BASE, headers=headers)
        elif method == "post":
            resp = _ban(client, BANNED_IP, headers)
        else:
            resp = client.delete(f"{BASE}/{BANNED_IP}", headers=headers)
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"
        assert RedisKeys.IP_BLACKLIST not in blacklist._data

    @pytest.mark.parametrize("method", ["get", "post", "delete"])
    def test_unauthenticated_is_rejected(self, client, blacklist, method):
        if method == "get":
            resp = client.get(BASE)
        elif method == "post":
            resp = client.post(BASE, json={"ip": BANNED_IP})
        else:
            resp = client.delete(f"{BASE}/{BANNED_IP}")
        assert resp.status_code == 401


class TestBlacklistEnforcement:
    """Middleware behavior through the real stack. The middleware resolves its
    redis client through its own name-bound import, which the conftest module
    patches do not cover — so these tests patch it to the module MockRedis."""

    def _patch_middleware_redis(self, mock_redis):
        return patch("app.core.middleware.get_redis_client", new=AsyncMock(return_value=mock_redis))

    def test_banned_ip_is_rejected_with_403(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        assert _ban(client, BANNED_IP, headers).status_code == 200
        with self._patch_middleware_redis(blacklist):
            resp = client.get("/api/v1/health", headers={"X-Forwarded-For": BANNED_IP})
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN_ENVELOPE

    def test_banned_ip_direct_connect_is_rejected(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        assert _ban(client, BANNED_IP, headers).status_code == 200
        blacklist._data[RedisKeys.IP_BLACKLIST] = {BANNED_IP, "testclient"}
        with self._patch_middleware_redis(blacklist):
            resp = client.get("/api/v1/health")
        assert resp.status_code == 403

    def test_unbanned_ip_passes(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        assert _ban(client, BANNED_IP, headers).status_code == 200
        with self._patch_middleware_redis(blacklist):
            resp = client.get("/api/v1/health", headers={"X-Forwarded-For": SECOND_IP})
        assert resp.status_code == 200

    def test_rightmost_xff_hop_decides(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        assert _ban(client, BANNED_IP, headers).status_code == 200
        with self._patch_middleware_redis(blacklist):
            # Genuine peer last: blocked.
            blocked = client.get("/api/v1/health", headers={"X-Forwarded-For": f"{SECOND_IP}, {BANNED_IP}"})
            assert blocked.status_code == 403
            # Banned IP only forged on the left: passes (rightmost wins).
            passed = client.get("/api/v1/health", headers={"X-Forwarded-For": f"{BANNED_IP}, {SECOND_IP}"})
            assert passed.status_code == 200

    def test_banned_ip_rejected_before_auth_and_routing(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        assert _ban(client, BANNED_IP, headers).status_code == 200
        with self._patch_middleware_redis(blacklist):
            # Even a perfectly valid admin request dies at the outermost layer.
            resp = client.get(BASE, headers={**headers, "X-Forwarded-For": BANNED_IP})
        assert resp.status_code == 403
        assert resp.json() == FORBIDDEN_ENVELOPE

    def test_unban_takes_effect_immediately(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        assert _ban(client, BANNED_IP, headers).status_code == 200
        with self._patch_middleware_redis(blacklist):
            assert client.get("/api/v1/health", headers={"X-Forwarded-For": BANNED_IP}).status_code == 403
            # DELETE invalidates the cache: unblock without waiting for the TTL.
            assert client.delete(f"{BASE}/{BANNED_IP}", headers=headers).status_code == 200
            resp = client.get("/api/v1/health", headers={"X-Forwarded-For": BANNED_IP})
        assert resp.status_code == 200

    def test_add_takes_effect_immediately_via_cache_invalidation(self, client, blacklist):
        headers, _t, _u = make_admin_headers()
        with self._patch_middleware_redis(blacklist):
            # Prime the cache with the empty set first.
            assert client.get("/api/v1/health", headers={"X-Forwarded-For": BANNED_IP}).status_code == 200
            assert _ban(client, BANNED_IP, headers).status_code == 200
            resp = client.get("/api/v1/health", headers={"X-Forwarded-For": BANNED_IP})
        assert resp.status_code == 403
