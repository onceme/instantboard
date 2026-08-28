"""Integration tests for the layer-2 Redis sliding-window rate limiter
(security.md §3.3) through the full app stack: enforcement via the real
middleware chain + MockRedis ZSET, envelope shape, and per-(tenant, IP)
bucket isolation. The limiter is disabled suite-wide in the integration
conftest; the rate_limiter fixture below re-enables it with clean buckets.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from tests.integration.conftest import make_auth_header

RATE_LIMIT_ENVELOPE = {
    "success": False,
    "error": {"code": "RATE_LIMIT_EXCEEDED", "message": "Rate limit exceeded", "details": None},
}

AUTH_PROVIDERS = "/api/v1/auth/sso/providers"


def _mock_category_service():
    # Keep the isolation test off the DB (a random-tenant UUID query binds
    # badly on SQLite); the limiter decides before routing anyway.
    mock_svc = AsyncMock()
    mock_svc.list_categories.return_value = {
        "success": True,
        "data": [],
        "meta": {"total": 0, "page": 1, "page_size": 20},
    }
    return mock_svc


@pytest.fixture
def rate_limiter(client, app_with_overrides, monkeypatch):
    """Enable the limiter for one test over freshly emptied rate buckets."""
    _app, mock_redis = app_with_overrides
    for key in [k for k in list(mock_redis._data) if isinstance(k, str) and k.startswith("rate:")]:
        mock_redis._data.pop(key, None)
        mock_redis._expiry.pop(key, None)
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    yield mock_redis


class TestRateLimitEnforcement:
    def test_auth_tier_returns_429_envelope_with_retry_after(self, client, rate_limiter, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 5)
        monkeypatch.setattr(settings, "rate_limit_burst", 0)
        for _ in range(5):
            assert client.get(AUTH_PROVIDERS).status_code == 200
        resp = client.get(AUTH_PROVIDERS)
        assert resp.status_code == 429
        # Middleware-built envelope: top level, NOT nested under "detail".
        assert resp.json() == RATE_LIMIT_ENVELOPE
        assert resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert resp.headers["Retry-After"] == "60"

    def test_429_sticks_while_the_window_is_full(self, client, rate_limiter, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 1)
        monkeypatch.setattr(settings, "rate_limit_burst", 0)
        assert client.get(AUTH_PROVIDERS).status_code == 200
        # Rejected requests are recorded as well, so an ongoing burst keeps
        # tripping until the window slides.
        for _ in range(3):
            assert client.get(AUTH_PROVIDERS).status_code == 429

    def test_exempt_health_path_is_never_limited(self, client, rate_limiter, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_per_minute", 1)
        monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 1)
        monkeypatch.setattr(settings, "rate_limit_burst", 0)
        for _ in range(8):
            assert client.get("/api/v1/health").status_code == 200

    def test_disabled_switch_lets_bursts_through(self, client, rate_limiter, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_enabled", False)
        monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 1)
        monkeypatch.setattr(settings, "rate_limit_burst", 0)
        for _ in range(5):
            assert client.get(AUTH_PROVIDERS).status_code == 200


class TestRateLimitBucketIsolation:
    def test_buckets_are_per_tenant(self, client, rate_limiter, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_per_minute", 2)
        monkeypatch.setattr(settings, "rate_limit_burst", 0)
        headers_a, tenant_a, _user_a = make_auth_header()
        headers_b, tenant_b, _user_b = make_auth_header()

        with patch("app.api.v1.categories._get_category_service", return_value=_mock_category_service()):
            for _ in range(2):
                assert client.get("/api/v1/categories", headers=headers_a).status_code == 200
            assert client.get("/api/v1/categories", headers=headers_a).status_code == 429

            # Tenant B shares the client IP but has its own bucket: unaffected.
            assert client.get("/api/v1/categories", headers=headers_b).status_code == 200
            assert client.get("/api/v1/categories", headers=headers_b).status_code == 200
            assert client.get("/api/v1/categories", headers=headers_b).status_code == 429

        assert f"rate:{tenant_a}:testclient:default" in rate_limiter._data
        assert f"rate:{tenant_b}:testclient:default" in rate_limiter._data

    def test_buckets_are_per_client_ip(self, client, rate_limiter, monkeypatch):
        monkeypatch.setattr(settings, "rate_limit_auth_per_minute", 1)
        monkeypatch.setattr(settings, "rate_limit_burst", 0)
        xff_a = {"X-Forwarded-For": "203.0.113.50"}
        xff_b = {"X-Forwarded-For": "203.0.113.51"}
        assert client.get(AUTH_PROVIDERS, headers=xff_a).status_code == 200
        # A different source IP is not collateral damage.
        assert client.get(AUTH_PROVIDERS, headers=xff_b).status_code == 200
        assert client.get(AUTH_PROVIDERS, headers=xff_a).status_code == 429
        assert client.get(AUTH_PROVIDERS, headers=xff_b).status_code == 429
