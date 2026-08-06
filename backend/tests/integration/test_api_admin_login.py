"""Integration tests for POST /api/v1/auth/admin/login and the local-admin kill-switch.

The local admin identity model (docs/design/admin-login.md) isolates the admin record in
the system tenant with provider='local' and never merges it with SSO users. These tests
exercise the real AuthService against the test database, using a unique admin email per
test so the shared module-scoped Redis/DB fixtures do not leak brute-force counters.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings
from app.core.constants import LOCAL_SSO_PROVIDER, SYSTEM_TENANT_ID
from app.core.security import decode_token, hash_password
from app.models.tenant import Tenant
from app.models.user import User
from tests.conftest import TEST_DATABASE_URL

ADMIN_PASSWORD = "correct horse battery staple"
# Computed once at import so every test verifies against a real bcrypt hash.
ADMIN_HASH = hash_password(ADMIN_PASSWORD)

LOGIN_URL = "/api/v1/auth/admin/login"
REFRESH_URL = "/api/v1/auth/refresh"
ME_URL = "/api/v1/auth/me"
SYSTEM_ENDPOINT = "/api/v1/dashboard/system"

SYNC_DB_URL = TEST_DATABASE_URL.replace("+aiosqlite", "")


def _enable_admin(monkeypatch, email):
    monkeypatch.setattr(settings, "admin_email", email)
    monkeypatch.setattr(settings, "admin_password_hash", ADMIN_HASH)


def _disable_admin(monkeypatch):
    monkeypatch.setattr(settings, "admin_email", None)
    monkeypatch.setattr(settings, "admin_password_hash", None)


@pytest.fixture(autouse=True)
def _raise_ip_threshold(monkeypatch):
    # The module-scoped MockRedis and the fixed test client IP would let the IP-dimension
    # failure counter accumulate across tests; keep only the email dimension meaningful.
    monkeypatch.setattr("app.services.auth.ADMIN_LOGIN_IP_MAX_FAILURES", 10**9)


def _unique_email():
    return f"admin-{uuid.uuid4().hex[:10]}@example.com"


def _login(client, email, password):
    return client.post(LOGIN_URL, json={"email": email, "password": password})


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _engine():
    return create_engine(SYNC_DB_URL, poolclass=NullPool, connect_args={"timeout": 30})


def _seed_sso_user(email):
    """Insert an SSO (github/member) user with the given email and return its ids."""
    engine = _engine()
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant = Tenant(name="SSO Tenant", slug=f"sso-{uuid.uuid4().hex[:10]}", plan="free", settings={})
    session.add(tenant)
    session.flush()
    user = User(
        tenant_id=tenant.id,
        email=email,
        name="SSO User",
        sso_provider="github",
        sso_provider_id=f"gh-{uuid.uuid4().hex}",
        role="member",
    )
    session.add(user)
    session.commit()
    tenant_id, user_id = tenant.id, user.id
    session.close()
    engine.dispose()
    return tenant_id, user_id


def _users_by_email(email):
    engine = _engine()
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    rows = session.execute(select(User).where(User.email == email)).scalars().all()
    data = [
        {
            "id": str(r.id),
            "tenant_id": str(r.tenant_id),
            "role": r.role,
            "sso_provider": r.sso_provider,
            "sso_provider_id": r.sso_provider_id,
        }
        for r in rows
    ]
    session.close()
    engine.dispose()
    return data


class TestAdminLoginHappyPath:
    def test_login_success_returns_admin_local_envelope(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        resp = _login(client, email, ADMIN_PASSWORD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        data = body["data"]
        assert data["token_type"] == "Bearer"
        assert data["access_token"]
        assert data["refresh_token"]
        assert data["user"]["role"] == "admin"
        assert data["user"]["sso_provider"] == LOCAL_SSO_PROVIDER
        assert data["user"]["tenant_id"] == str(SYSTEM_TENANT_ID)
        claims = decode_token(data["access_token"])
        assert claims["role"] == "admin"
        assert claims["provider"] == LOCAL_SSO_PROVIDER
        assert claims["tenant_id"] == str(SYSTEM_TENANT_ID)

    def test_login_then_me_returns_admin(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        data = _login(client, email, ADMIN_PASSWORD).json()["data"]
        resp = client.get(ME_URL, headers=_bearer(data["access_token"]))
        assert resp.status_code == 200
        me = resp.json()["data"]
        assert me["role"] == "admin"
        assert me["sso_provider"] == LOCAL_SSO_PROVIDER
        assert me["tenant_id"] == str(SYSTEM_TENANT_ID)
        assert me["email"] == email

    def test_admin_token_passes_require_admin_dashboard(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        data = _login(client, email, ADMIN_PASSWORD).json()["data"]
        with patch("app.api.v1.dashboard.DashboardService") as mock_svc_cls:
            mock_svc = AsyncMock()
            mock_svc.get_system_info.return_value = {
                "version": "1.0.0",
                "uptime_seconds": 3600,
                "environment": "test",
                "python_version": "3.12.3",
                "cpu_count": 8,
                "cpu_usage_percent": 25.0,
                "memory_total_mb": 16384,
                "memory_used_mb": 8192,
                "disk_total_gb": 500.0,
                "disk_used_gb": 200.0,
            }
            mock_svc_cls.return_value = mock_svc
            resp = client.get(SYSTEM_ENDPOINT, headers=_bearer(data["access_token"]))
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == "1.0.0"


class TestAdminLoginFailures:
    def test_wrong_password_401_invalid_credentials(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        resp = _login(client, email, "wrong-password")
        assert resp.status_code == 401
        err = resp.json()["detail"]["error"]
        assert err["code"] == "INVALID_CREDENTIALS"
        # Unified message: must not reveal whether the email exists or password matched.
        assert "email or password" in err["message"].lower()

    def test_unknown_email_401_same_code(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        resp = _login(client, "someone-else-" + email, ADMIN_PASSWORD)
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_CREDENTIALS"

    def test_disabled_returns_503(self, client, monkeypatch):
        _disable_admin(monkeypatch)
        resp = _login(client, _unique_email(), ADMIN_PASSWORD)
        assert resp.status_code == 503
        assert resp.json()["detail"]["error"]["code"] == "ADMIN_LOGIN_DISABLED"

    def test_invalid_email_shape_422(self, client, monkeypatch):
        _enable_admin(monkeypatch, _unique_email())
        resp = client.post(LOGIN_URL, json={"email": "not-an-email", "password": ADMIN_PASSWORD})
        assert resp.status_code == 422

    def test_password_too_long_422(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        resp = client.post(LOGIN_URL, json={"email": email, "password": "x" * 73})
        assert resp.status_code == 422


class TestAdminLoginLockout:
    def test_five_failures_lock_even_correct_password(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        for _ in range(5):
            resp = _login(client, email, "wrong-password")
            assert resp.status_code == 401
        # Locked: the correct password is now rejected too.
        resp = _login(client, email, ADMIN_PASSWORD)
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_CREDENTIALS"


class TestIsolationFromSSO:
    def test_admin_login_does_not_touch_same_email_sso_user(self, client, monkeypatch):
        email = _unique_email()
        sso_tenant_id, sso_user_id = _seed_sso_user(email)
        _enable_admin(monkeypatch, email)

        resp = _login(client, email, ADMIN_PASSWORD)
        assert resp.status_code == 200

        rows = _users_by_email(email)
        assert len(rows) == 2
        by_provider = {r["sso_provider"]: r for r in rows}

        sso_row = by_provider["github"]
        assert sso_row["id"] == str(sso_user_id)
        assert sso_row["role"] == "member"
        assert sso_row["tenant_id"] == str(sso_tenant_id)

        admin_row = by_provider[LOCAL_SSO_PROVIDER]
        assert admin_row["role"] == "admin"
        assert admin_row["tenant_id"] == str(SYSTEM_TENANT_ID)
        assert admin_row["sso_provider_id"] == f"{LOCAL_SSO_PROVIDER}:{email}"
        assert admin_row["id"] != str(sso_user_id)


class TestRefreshKillSwitch:
    def test_kill_switch_blocks_then_allows_local_refresh(self, client, monkeypatch):
        email = _unique_email()
        _enable_admin(monkeypatch, email)
        data = _login(client, email, ADMIN_PASSWORD).json()["data"]
        refresh_token = data["refresh_token"]

        # Disable admin login -> existing local sessions can no longer renew.
        _disable_admin(monkeypatch)
        resp = client.post(REFRESH_URL, json={"refresh_token": refresh_token})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_REFRESH_TOKEN"

        # Re-enable -> the same refresh token works again (kill-switch does not blacklist).
        _enable_admin(monkeypatch, email)
        resp = client.post(REFRESH_URL, json={"refresh_token": refresh_token})
        assert resp.status_code == 200
        assert resp.json()["data"]["access_token"]
