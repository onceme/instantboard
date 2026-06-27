import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import (
    AuthRequired,
    InvalidOAuthCode,
    InvalidRefreshToken,
    InvalidToken,
    SSOProviderError,
    ValidationError,
)
from app.services.auth import AuthService


def _make_user(
    user_id=None,
    tenant_id=None,
    email="test@example.com",
    name="Test User",
    role="member",
    sso_provider="google",
    sso_provider_id="12345",
    avatar_url="https://example.com/avatar.png",
    last_login_at=None,
):
    user = MagicMock()
    user.id = user_id or uuid.uuid4()
    user.tenant_id = tenant_id or uuid.uuid4()
    user.email = email
    user.name = name
    user.role = role
    user.sso_provider = sso_provider
    user.sso_provider_id = sso_provider_id
    user.avatar_url = avatar_url
    user.last_login_at = last_login_at
    return user


def _make_tenant(tenant_id=None, slug="default", name="Default Tenant"):
    tenant = MagicMock()
    tenant.id = tenant_id or uuid.uuid4()
    tenant.slug = slug
    tenant.name = name
    return tenant


def _make_sso_user_info(provider_id="12345", email="user@example.com", name="SSO User", avatar_url=None, provider="google"):
    info = MagicMock()
    info.provider_id = provider_id
    info.email = email
    info.name = name
    info.avatar_url = avatar_url
    info.provider = provider
    return info


def _mock_db_session():
    session = AsyncMock()
    mock_result = MagicMock()
    session.execute = AsyncMock(return_value=mock_result)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.flush = AsyncMock()
    session.add = MagicMock()
    return session, mock_result


def _mock_redis():
    redis = AsyncMock()
    redis.scan_iter = AsyncMock(return_value=iter([]))
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    return redis


class TestSSOLogin:
    @patch("app.services.auth.redis_set")
    @patch("app.services.auth.create_refresh_token", return_value="refresh_tok")
    @patch("app.services.auth.create_access_token", return_value="access_tok")
    @patch("app.services.auth.SSOHandlerFactory")
    async def test_sso_login_success(
        self, mock_factory, mock_create_access, mock_create_refresh, mock_redis_set
    ):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        user = _make_user()

        with patch("app.services.auth.AuthService._get_or_create_user", new_callable=AsyncMock, return_value=user):
            handler = AsyncMock()
            sso_info = _make_sso_user_info()
            handler.authenticate = AsyncMock(return_value=sso_info)
            handler.close = AsyncMock()
            mock_factory.create.return_value = handler

            service = AuthService(db, redis)
            result = await service.sso_login("google", "code123", "https://redirect.example.com")

            assert result["access_token"] == "access_tok"
            assert result["refresh_token"] == "refresh_tok"
            assert result["token_type"] == "Bearer"
            assert "expires_in" in result
            assert result["user"]["email"] == user.email
            handler.authenticate.assert_called_once_with("code123", "https://redirect.example.com")
            handler.close.assert_called_once()

    async def test_sso_login_unsupported_provider(self):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        service = AuthService(db, redis)

        with pytest.raises(ValidationError, match="Unsupported SSO provider"):
            await service.sso_login("invalid_provider", "code", "https://redirect.example.com")

    @patch("app.services.auth.SSOHandlerFactory")
    async def test_sso_login_auth_failure_value_error(self, mock_factory):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        handler = AsyncMock()
        handler.authenticate = AsyncMock(side_effect=ValueError("bad code"))
        handler.close = AsyncMock()
        mock_factory.create.return_value = handler

        service = AuthService(db, redis)
        with pytest.raises(SSOProviderError, match="authentication failed"):
            await service.sso_login("google", "bad_code", "https://redirect.example.com")
        handler.close.assert_called_once()

    @patch("app.services.auth.SSOHandlerFactory")
    async def test_sso_login_auth_failure_general_exception(self, mock_factory):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        handler = AsyncMock()
        handler.authenticate = AsyncMock(side_effect=RuntimeError("provider down"))
        handler.close = AsyncMock()
        mock_factory.create.return_value = handler

        service = AuthService(db, redis)
        with pytest.raises(SSOProviderError, match="returned an error"):
            await service.sso_login("google", "bad_code", "https://redirect.example.com")
        handler.close.assert_called_once()

    @patch("app.services.auth.SSOHandlerFactory")
    async def test_sso_login_no_provider_id(self, mock_factory):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        handler = AsyncMock()
        sso_info = _make_sso_user_info(provider_id=None)
        handler.authenticate = AsyncMock(return_value=sso_info)
        handler.close = AsyncMock()
        mock_factory.create.return_value = handler

        service = AuthService(db, redis)
        with pytest.raises(InvalidOAuthCode, match="no user ID"):
            await service.sso_login("google", "code", "https://redirect.example.com")


class TestRefreshToken:
    @patch("app.services.auth.create_refresh_token", return_value="new_refresh")
    @patch("app.services.auth.create_access_token", return_value="new_access")
    @patch("app.services.auth.add_token_to_blacklist", new_callable=AsyncMock)
    @patch("app.services.auth.is_refresh_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("app.services.auth.decode_token")
    async def test_refresh_token_success(
        self, mock_decode, mock_blacklist_check, mock_blacklist_add, mock_create_access, mock_create_refresh
    ):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        user = _make_user()
        mock_result.scalar_one_or_none.return_value = user

        mock_decode.return_value = {
            "sub": str(user.id),
            "type": "refresh",
            "provider": "google",
            "refresh_version": "v1",
        }

        service = AuthService(db, redis)
        result = await service.refresh_token("old_refresh_token")

        assert result["access_token"] == "new_access"
        assert result["refresh_token"] == "new_refresh"
        assert "expires_in" in result

    @patch("app.services.auth.decode_token", return_value=None)
    async def test_refresh_token_invalid_decode(self, mock_decode):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        service = AuthService(db, redis)

        with pytest.raises(InvalidRefreshToken):
            await service.refresh_token("invalid_token")

    @patch("app.services.auth.decode_token")
    async def test_refresh_token_wrong_type(self, mock_decode):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        mock_decode.return_value = {"sub": "user-id", "type": "access"}

        service = AuthService(db, redis)
        with pytest.raises(InvalidRefreshToken):
            await service.refresh_token("access_token_instead")

    @patch("app.services.auth.is_refresh_token_blacklisted", new_callable=AsyncMock, return_value=True)
    @patch("app.services.auth.decode_token")
    async def test_refresh_token_blacklisted(self, mock_decode, mock_blacklist_check):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        mock_decode.return_value = {"sub": "user-id", "type": "refresh", "refresh_version": "v1"}

        service = AuthService(db, redis)
        with pytest.raises(InvalidRefreshToken, match="revoked"):
            await service.refresh_token("refresh_tok")

    @patch("app.services.auth.is_refresh_token_blacklisted", new_callable=AsyncMock, return_value=False)
    @patch("app.services.auth.decode_token")
    async def test_refresh_token_user_not_found(self, mock_decode, mock_blacklist_check):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        mock_decode.return_value = {"sub": str(uuid.uuid4()), "type": "refresh"}
        mock_result.scalar_one_or_none.return_value = None

        service = AuthService(db, redis)
        with pytest.raises(InvalidRefreshToken, match="User not found"):
            await service.refresh_token("refresh_tok")


class TestGetCurrentUser:
    async def test_get_current_user_success(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        tenant_id = uuid.uuid4()
        user = _make_user(tenant_id=tenant_id)
        mock_result.scalar_one_or_none.return_value = user

        service = AuthService(db, redis)
        result = await service.get_current_user(str(user.id), str(tenant_id))

        assert result["id"] == str(user.id)
        assert result["email"] == user.email

    async def test_get_current_user_not_found(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = AuthService(db, redis)
        with pytest.raises(InvalidToken, match="User not found"):
            await service.get_current_user(str(uuid.uuid4()), str(uuid.uuid4()))

    async def test_get_current_user_tenant_mismatch(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        user = _make_user(tenant_id=uuid.uuid4())
        mock_result.scalar_one_or_none.return_value = user

        service = AuthService(db, redis)
        with pytest.raises(AuthRequired, match="Tenant mismatch"):
            await service.get_current_user(str(user.id), str(uuid.uuid4()))


class TestLogout:
    @patch("app.services.auth.redis_delete", new_callable=AsyncMock)
    @patch("app.services.auth.add_token_to_blacklist", new_callable=AsyncMock)
    @patch("app.services.auth.get_access_token_remaining_seconds", return_value=100)
    async def test_logout_with_access_token_only(self, mock_remaining, mock_blacklist, mock_redis_delete):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        redis.scan_iter = MagicMock(return_value=_async_iter([]))
        service = AuthService(db, redis)

        result = await service.logout("access_token")
        assert result == {"message": "Logged out"}
        mock_blacklist.assert_called_once_with("access_token", ttl_seconds=100)

    @patch("app.services.auth.redis_delete", new_callable=AsyncMock)
    @patch("app.services.auth.add_token_to_blacklist", new_callable=AsyncMock)
    @patch("app.services.auth.get_access_token_remaining_seconds", return_value=0)
    async def test_logout_expired_access_token(self, mock_remaining, mock_blacklist, mock_redis_delete):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        redis.scan_iter = MagicMock(return_value=_async_iter([]))
        service = AuthService(db, redis)

        result = await service.logout("expired_token")
        assert result == {"message": "Logged out"}
        mock_blacklist.assert_not_called()

    @patch("app.services.auth.add_token_to_blacklist", new_callable=AsyncMock)
    @patch("app.services.auth.get_access_token_remaining_seconds", return_value=50)
    async def test_logout_with_refresh_token(self, mock_remaining, mock_blacklist):
        db, _ = _mock_db_session()
        redis = _mock_redis()
        redis.scan_iter = MagicMock(return_value=_async_iter([]))
        service = AuthService(db, redis)

        result = await service.logout("access_tok", refresh_token_str="refresh_tok")
        assert result == {"message": "Logged out"}
        assert mock_blacklist.call_count == 2

    @patch("app.services.auth.redis_delete", new_callable=AsyncMock)
    @patch("app.services.auth.add_token_to_blacklist", new_callable=AsyncMock)
    @patch("app.services.auth.get_access_token_remaining_seconds", return_value=0)
    async def test_logout_with_user_id_session_cleanup(self, mock_remaining, mock_blacklist, mock_redis_del):
        db, _ = _mock_db_session()
        redis = _mock_redis()

        user_id = "user-123"
        session_data = json.dumps({"user_id": user_id, "tenant_id": "t1"})

        async def mock_scan_iter(match=None):
            yield "session:abc"
            yield "session:def"

        redis.scan_iter = mock_scan_iter
        redis.get = AsyncMock(side_effect=[session_data, json.dumps({"user_id": "other-user"})])

        service = AuthService(db, redis)
        result = await service.logout("access_tok", user_id=user_id)
        assert result == {"message": "Logged out"}
        mock_redis_del.assert_called_once_with("session:abc")

    @patch("app.services.auth.redis_delete", new_callable=AsyncMock)
    @patch("app.services.auth.add_token_to_blacklist", new_callable=AsyncMock)
    @patch("app.services.auth.get_access_token_remaining_seconds", return_value=0)
    async def test_logout_session_invalid_json_skipped(self, mock_remaining, mock_blacklist, mock_redis_del):
        db, _ = _mock_db_session()
        redis = _mock_redis()

        async def mock_scan_iter(match=None):
            yield "session:bad"

        redis.scan_iter = mock_scan_iter
        redis.get = AsyncMock(return_value="not-json{{{")

        service = AuthService(db, redis)
        result = await service.logout("access_tok", user_id="u1")
        assert result == {"message": "Logged out"}
        mock_redis_del.assert_not_called()


class TestGetOrCreateUser:
    async def test_existing_user_update(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        user = _make_user(email="old@example.com", name="Old Name", avatar_url=None)
        mock_result.scalar_one_or_none.return_value = user

        sso_info = _make_sso_user_info(
            email="new@example.com", name="New Name", avatar_url="https://new.com/pic.png"
        )

        service = AuthService(db, redis)
        result = await service._get_or_create_user("google", sso_info)

        assert result == user
        assert user.email == "new@example.com"
        assert user.name == "New Name"
        assert user.avatar_url == "https://new.com/pic.png"
        db.flush.assert_called()

    async def test_existing_user_no_update_needed(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        user = _make_user(email="same@example.com", name="Same Name", avatar_url=None)
        mock_result.scalar_one_or_none.return_value = user

        sso_info = _make_sso_user_info(email=None, name=None, avatar_url=None)

        service = AuthService(db, redis)
        result = await service._get_or_create_user("google", sso_info)
        assert result == user

    async def test_new_user_by_email_match(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()

        existing_user = _make_user(email="match@example.com", name="Existing")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = existing_user
            return mock_r

        db.execute = execute_side_effect

        sso_info = _make_sso_user_info(email="match@example.com", name="New SSO Name", avatar_url="https://av.com/a.png")

        service = AuthService(db, redis)
        result = await service._get_or_create_user("github", sso_info)

        assert result == existing_user
        assert existing_user.sso_provider == "github"

    async def test_new_user_creation(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()

        tenant = _make_tenant()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count in (1, 2):
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = tenant
            return mock_r

        db.execute = execute_side_effect

        sso_info = _make_sso_user_info(email="brand_new@example.com", name="Brand New")

        service = AuthService(db, redis)
        result = await service._get_or_create_user("google", sso_info)

        db.add.assert_called_once()
        assert result.email == "brand_new@example.com"
        assert result.name == "Brand New"

    async def test_new_user_no_email(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        tenant = _make_tenant()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = tenant
            return mock_r

        db.execute = execute_side_effect

        sso_info = _make_sso_user_info(email=None, name=None)

        service = AuthService(db, redis)
        result = await service._get_or_create_user("google", sso_info)

        assert "google_" in result.email
        assert result.name == "google User"


class TestGetDefaultTenant:
    async def test_existing_tenant(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        tenant = _make_tenant()
        mock_result.scalar_one_or_none.return_value = tenant

        service = AuthService(db, redis)
        result = await service._get_default_tenant()
        assert result == tenant

    async def test_create_new_tenant(self):
        db, mock_result = _mock_db_session()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = AuthService(db, redis)
        result = await service._get_default_tenant()

        db.add.assert_called_once()
        assert result.slug == "default"
        assert result.name == "Default Tenant"


async def _async_iter(items):
    for item in items:
        yield item
