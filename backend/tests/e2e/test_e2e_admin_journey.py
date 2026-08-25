"""E2E: local admin journey.

Configure admin credentials -> POST /auth/admin/login -> admin-only /dashboard/system
-> GET /auth/me (role=local admin) -> refresh rotation (old refresh revoked) ->
logout -> blacklisted access token rejected.
"""

import uuid

import pytest

from app.config import settings
from app.core.constants import LOCAL_SSO_PROVIDER, SYSTEM_TENANT_ID
from app.core.security import create_access_token, hash_password
from tests.e2e.conftest import bearer

pytestmark = pytest.mark.e2e

ADMIN_PASSWORD = "correct horse battery staple"
ADMIN_HASH = hash_password(ADMIN_PASSWORD)


@pytest.fixture(autouse=True)
def _raise_ip_threshold(monkeypatch):
    # Shared mock redis + fixed test IP: keep the IP-dimension lockout counter inert
    # so only the per-email dimension is meaningful (same guard as the integration suite).
    monkeypatch.setattr("app.services.auth.ADMIN_LOGIN_IP_MAX_FAILURES", 10**9)


@pytest.fixture
def admin_config(monkeypatch):
    email = f"e2e-admin-{uuid.uuid4().hex[:10]}@example.com"
    monkeypatch.setattr(settings, "admin_email", email)
    monkeypatch.setattr(settings, "admin_password_hash", ADMIN_HASH)
    return email


class TestLocalAdminJourney:
    async def test_full_admin_session_lifecycle(self, aclient, admin_config):
        email = admin_config

        resp = await aclient.post("/api/v1/auth/admin/login", json={"email": email, "password": "wrong-password"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_CREDENTIALS"

        resp = await aclient.post("/api/v1/auth/admin/login", json={"email": email, "password": ADMIN_PASSWORD})
        assert resp.status_code == 200
        data = resp.json()["data"]
        access_token = data["access_token"]
        refresh_token = data["refresh_token"]
        assert data["token_type"] == "Bearer"
        assert data["expires_in"] > 0
        assert data["user"]["role"] == "admin"
        assert data["user"]["sso_provider"] == LOCAL_SSO_PROVIDER
        assert data["user"]["tenant_id"] == str(SYSTEM_TENANT_ID)

        resp = await aclient.get("/api/v1/dashboard/system", headers=bearer(access_token))
        assert resp.status_code == 200
        system_info = resp.json()["data"]
        assert system_info["version"] == "1.0.0"
        assert system_info["uptime_seconds"] >= 0
        assert "cpu_usage_percent" in system_info

        resp = await aclient.get("/api/v1/auth/me", headers=bearer(access_token))
        assert resp.status_code == 200
        me = resp.json()["data"]
        assert me["role"] == "admin"
        assert me["sso_provider"] == LOCAL_SSO_PROVIDER
        assert me["tenant_id"] == str(SYSTEM_TENANT_ID)
        assert me["email"] == email

        resp = await aclient.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        refreshed = resp.json()["data"]
        new_access_token = refreshed["access_token"]
        new_refresh_token = refreshed["refresh_token"]
        assert new_access_token != access_token

        # Rotation blacklists the consumed refresh token; replaying it must fail.
        resp = await aclient.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_REFRESH_TOKEN"

        resp = await aclient.get("/api/v1/auth/me", headers=bearer(new_access_token))
        assert resp.status_code == 200

        resp = await aclient.request(
            "DELETE",
            "/api/v1/auth/logout",
            headers=bearer(new_access_token),
            json={"refresh_token": new_refresh_token},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["message"] == "Logged out"

        # Blacklist must now reject the still-unexpired access token.
        resp = await aclient.get("/api/v1/auth/me", headers=bearer(new_access_token))
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

        resp = await aclient.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh_token})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_REFRESH_TOKEN"

    async def test_dashboard_system_rejects_non_admin(self, aclient):
        member_token = create_access_token(
            {
                "sub": str(uuid.uuid4()),
                "tenant_id": str(uuid.uuid4()),
                "role": "member",
                "provider": "github",
            }
        )
        resp = await aclient.get("/api/v1/dashboard/system", headers=bearer(member_token))
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"

    async def test_admin_login_disabled_returns_503(self, aclient, admin_config, monkeypatch):
        resp = await aclient.post(
            "/api/v1/auth/admin/login",
            json={"email": admin_config, "password": ADMIN_PASSWORD},
        )
        assert resp.status_code == 200

        monkeypatch.setattr(settings, "admin_email", None)
        monkeypatch.setattr(settings, "admin_password_hash", None)
        resp = await aclient.post(
            "/api/v1/auth/admin/login",
            json={"email": admin_config, "password": ADMIN_PASSWORD},
        )
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"]["code"] == "ADMIN_LOGIN_DISABLED"
