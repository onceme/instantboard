"""Unit tests for AuthService.admin_login and the local-admin refresh kill-switch.

The local admin identity model is defined in docs/dev-guide/design/admin-login.md: the admin
record lives in the system tenant with provider='local' and is isolated from SSO users.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import LOCAL_SSO_PROVIDER, SYSTEM_TENANT_ID
from app.core.exceptions import AdminLoginDisabled, InvalidCredentials, InvalidRefreshToken
from app.core.redis import RedisKeys
from app.core.security import create_refresh_token, decode_token
from app.services.auth import _DUMMY_BCRYPT_HASH, AuthService

ADMIN_EMAIL = "admin@example.com"
ADMIN_HASH = "$2b$12$notarealhashbutneververified"
CLIENT_IP = "203.0.113.7"


def _fake_settings(*, enabled=True, email=ADMIN_EMAIL, pw_hash=ADMIN_HASH) -> SimpleNamespace:
    return SimpleNamespace(
        admin_login_enabled=enabled,
        admin_email=email,
        admin_password_hash=pw_hash,
        jwt_access_token_expire_minutes=60,
    )


def _fake_user(email=ADMIN_EMAIL, tenant_id=SYSTEM_TENANT_ID, role="admin") -> MagicMock:
    user = MagicMock()
    user.id = uuid.uuid4()
    user.tenant_id = tenant_id
    user.email = email
    user.name = email.split("@", 1)[0]
    user.avatar_url = None
    user.role = role
    user.sso_provider = LOCAL_SSO_PROVIDER
    user.sso_provider_id = f"{LOCAL_SSO_PROVIDER}:{email}"
    user.last_login_at = None
    return user


def _mock_db(lookup_user=None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = lookup_user
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


def _mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.incr = AsyncMock(return_value=1)
    redis.expire = AsyncMock(return_value=True)
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _service(db=None, redis=None) -> AuthService:
    return AuthService(db or _mock_db(), redis or _mock_redis())


class TestAdminLoginDisabled:
    async def test_disabled_raises_admin_login_disabled(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=False))
        svc = _service()
        with pytest.raises(AdminLoginDisabled):
            await svc.admin_login(ADMIN_EMAIL, "any-password", CLIENT_IP)


class TestAdminLoginLocks:
    async def test_email_lock_rejects_before_db_and_verify(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        db = _mock_db()
        redis = _mock_redis()
        lock_key = RedisKeys.admin_login_lock_key(ADMIN_EMAIL)
        redis.get = AsyncMock(side_effect=lambda key: "1" if key == lock_key else None)
        svc = _service(db, redis)
        with patch("app.services.auth.verify_password") as mock_verify, pytest.raises(InvalidCredentials):
            await svc.admin_login(ADMIN_EMAIL, "any-password", CLIENT_IP)
        mock_verify.assert_not_called()
        db.execute.assert_not_called()

    async def test_ip_lock_rejects_before_db_and_verify(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        db = _mock_db()
        redis = _mock_redis()
        lock_key = RedisKeys.admin_login_lock_ip_key(CLIENT_IP)
        redis.get = AsyncMock(side_effect=lambda key: "1" if key == lock_key else None)
        svc = _service(db, redis)
        with patch("app.services.auth.verify_password") as mock_verify, pytest.raises(InvalidCredentials):
            await svc.admin_login(ADMIN_EMAIL, "any-password", CLIENT_IP)
        mock_verify.assert_not_called()
        db.execute.assert_not_called()

    async def test_lock_check_fails_open_when_redis_down(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        db = _mock_db()
        redis = _mock_redis()
        redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
        user = _fake_user()
        svc = _service(db, redis)
        with (
            patch("app.services.auth.verify_password", return_value=True),
            patch.object(AuthService, "_upsert_admin_user", AsyncMock(return_value=user)),
        ):
            result = await svc.admin_login(ADMIN_EMAIL, "any-password", CLIENT_IP)
        assert result["user"]["role"] == "admin"


class TestAdminLoginFailure:
    async def test_wrong_password_raises_invalid_credentials(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        redis = _mock_redis()
        svc = _service(_mock_db(lookup_user=_fake_user()), redis)
        with (
            patch("app.services.auth.verify_password", return_value=False) as mock_verify,
            pytest.raises(InvalidCredentials),
        ):
            await svc.admin_login(ADMIN_EMAIL, "wrong-password", CLIENT_IP)
        mock_verify.assert_called_once_with("wrong-password", ADMIN_HASH)

    async def test_wrong_password_increments_both_counters(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        redis = _mock_redis()
        svc = _service(_mock_db(lookup_user=_fake_user()), redis)
        with patch("app.services.auth.verify_password", return_value=False), pytest.raises(InvalidCredentials):
            await svc.admin_login(ADMIN_EMAIL, "wrong-password", CLIENT_IP)
        assert redis.incr.await_count == 2
        redis.incr.assert_any_await(RedisKeys.admin_login_fail_key(ADMIN_EMAIL))
        redis.incr.assert_any_await(RedisKeys.admin_login_fail_ip_key(CLIENT_IP))

    async def test_unknown_email_runs_dummy_verify_and_fails(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        svc = _service(_mock_db(lookup_user=None), _mock_redis())
        with (
            patch("app.services.auth.verify_password", return_value=False) as mock_verify,
            pytest.raises(InvalidCredentials),
        ):
            await svc.admin_login("someone-else@example.com", "any-password", CLIENT_IP)
        # Timing equalization: the dummy hash is verified, not the configured one.
        mock_verify.assert_called_once_with("any-password", _DUMMY_BCRYPT_HASH)

    async def test_email_is_normalized_before_match(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        svc = _service(_mock_db(lookup_user=_fake_user()), _mock_redis())
        with (
            patch("app.services.auth.verify_password", return_value=False) as mock_verify,
            pytest.raises(InvalidCredentials),
        ):
            await svc.admin_login("  ADMIN@Example.COM ", "pw", CLIENT_IP)
        # Normalized email matched the configured email, so the real hash was used.
        mock_verify.assert_called_once_with("pw", ADMIN_HASH)


class TestAdminLoginSuccess:
    async def test_success_returns_admin_local_token_and_claims(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        user = _fake_user()
        db = _mock_db(lookup_user=None)
        redis = _mock_redis()
        svc = _service(db, redis)
        with (
            patch("app.services.auth.verify_password", return_value=True),
            patch.object(AuthService, "_upsert_admin_user", AsyncMock(return_value=user)),
        ):
            result = await svc.admin_login(ADMIN_EMAIL, "correct-password", CLIENT_IP)

        claims = decode_token(result["access_token"])
        assert claims["provider"] == LOCAL_SSO_PROVIDER
        assert claims["role"] == "admin"
        assert claims["tenant_id"] == str(SYSTEM_TENANT_ID)
        assert claims["sub"] == str(user.id)
        assert result["token_type"] == "Bearer"
        assert result["user"]["role"] == "admin"
        assert result["user"]["sso_provider"] == LOCAL_SSO_PROVIDER
        db.commit.assert_awaited_once()

    async def test_success_clears_failure_counter(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        user = _fake_user()
        redis = _mock_redis()
        svc = _service(_mock_db(lookup_user=None), redis)
        with (
            patch("app.services.auth.verify_password", return_value=True),
            patch.object(AuthService, "_upsert_admin_user", AsyncMock(return_value=user)),
        ):
            await svc.admin_login(ADMIN_EMAIL, "correct-password", CLIENT_IP)
        redis.delete.assert_awaited_once_with(RedisKeys.admin_login_fail_key(ADMIN_EMAIL))

    async def test_success_after_lock_failure_does_not_record_new_failure(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        user = _fake_user()
        redis = _mock_redis()
        svc = _service(_mock_db(lookup_user=None), redis)
        with (
            patch("app.services.auth.verify_password", return_value=True),
            patch.object(AuthService, "_upsert_admin_user", AsyncMock(return_value=user)),
        ):
            await svc.admin_login(ADMIN_EMAIL, "correct-password", CLIENT_IP)
        redis.incr.assert_not_awaited()


class TestUpsertAdminUser:
    async def test_creates_admin_in_system_tenant_when_missing(self):
        db = _mock_db(lookup_user=None)
        svc = _service(db)
        system_tenant = MagicMock()
        system_tenant.id = SYSTEM_TENANT_ID
        with patch.object(AuthService, "_get_system_tenant", AsyncMock(return_value=system_tenant)):
            user = await svc._upsert_admin_user(ADMIN_EMAIL, None)
        assert user.tenant_id == SYSTEM_TENANT_ID
        assert user.email == ADMIN_EMAIL
        assert user.role == "admin"
        assert user.sso_provider == LOCAL_SSO_PROVIDER
        assert user.sso_provider_id == f"{LOCAL_SSO_PROVIDER}:{ADMIN_EMAIL}"
        db.add.assert_called_once()

    async def test_promotes_existing_record_to_admin(self):
        svc = _service()
        existing = _fake_user(role="member")
        user = await svc._upsert_admin_user(ADMIN_EMAIL, existing)
        assert user is existing
        assert user.role == "admin"

    async def test_local_provider_id_shape(self):
        assert AuthService._local_provider_id(ADMIN_EMAIL) == f"{LOCAL_SSO_PROVIDER}:{ADMIN_EMAIL}"


class TestGetSystemTenant:
    async def test_creates_system_tenant_when_missing(self):
        db = _mock_db(lookup_user=None)
        svc = _service(db)
        tenant = await svc._get_system_tenant()
        assert tenant.id == SYSTEM_TENANT_ID
        assert tenant.slug == "system"
        assert tenant.plan == "enterprise"
        db.add.assert_called_once()

    async def test_returns_existing_system_tenant(self):
        existing = MagicMock()
        existing.id = SYSTEM_TENANT_ID
        db = _mock_db(lookup_user=existing)
        svc = _service(db)
        tenant = await svc._get_system_tenant()
        assert tenant is existing
        db.add.assert_not_called()


class TestRefreshKillSwitch:
    def _local_refresh_token(self) -> str:
        return create_refresh_token(
            {
                "sub": str(uuid.uuid4()),
                "tenant_id": str(SYSTEM_TENANT_ID),
                "role": "admin",
                "provider": LOCAL_SSO_PROVIDER,
            }
        )

    async def test_local_refresh_rejected_when_admin_login_disabled(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=False))
        db = _mock_db()
        svc = _service(db, _mock_redis())
        with pytest.raises(InvalidRefreshToken, match="Admin login is disabled"):
            await svc.refresh_token(self._local_refresh_token())
        # Rejected before any DB lookup.
        db.execute.assert_not_called()

    async def test_local_refresh_allowed_when_admin_login_enabled(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=True))
        user = _fake_user()
        db = _mock_db(lookup_user=user)
        svc = _service(db, _mock_redis())
        with (
            patch("app.services.auth.is_refresh_token_blacklisted", AsyncMock(return_value=False)),
            patch("app.services.auth.add_token_to_blacklist", AsyncMock(return_value=None)),
        ):
            result = await svc.refresh_token(self._local_refresh_token())
        assert "access_token" in result
        assert "refresh_token" in result

    async def test_non_local_refresh_unaffected_when_admin_login_disabled(self, monkeypatch):
        monkeypatch.setattr("app.services.auth.settings", _fake_settings(enabled=False))
        user = _fake_user(role="member")
        user.sso_provider = "github"
        db = _mock_db(lookup_user=user)
        svc = _service(db, _mock_redis())
        token = create_refresh_token(
            {
                "sub": str(user.id),
                "tenant_id": str(user.tenant_id),
                "role": "member",
                "provider": "github",
            }
        )
        with (
            patch("app.services.auth.is_refresh_token_blacklisted", AsyncMock(return_value=False)),
            patch("app.services.auth.add_token_to_blacklist", AsyncMock(return_value=None)),
        ):
            result = await svc.refresh_token(token)
        assert "access_token" in result
