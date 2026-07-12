import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from jose import jwt

from app.config import settings
from app.core.security import (
    add_token_to_blacklist,
    blacklist_refresh_token,
    create_access_token,
    create_refresh_token,
    decode_token,
    extract_user_from_token,
    get_access_token_remaining_seconds,
    hash_password,
    is_refresh_token_blacklisted,
    is_token_blacklisted,
    verify_password,
)


class TestHashPassword:
    def test_hash(self):
        with patch("app.core.security.pwd_context") as mock_ctx:
            mock_ctx.hash.return_value = "$2b$12$hashed_pw_string"
            result = hash_password("password123")
            assert result == "$2b$12$hashed_pw_string"
            mock_ctx.hash.assert_called_once_with("password123")


class TestVerifyPassword:
    def test_correct_password(self):
        with patch("app.core.security.pwd_context") as mock_ctx:
            mock_ctx.verify.return_value = True
            result = verify_password("password123", "$2b$12$hashed")
            assert result is True
            mock_ctx.verify.assert_called_once_with("password123", "$2b$12$hashed")

    def test_incorrect_password(self):
        with patch("app.core.security.pwd_context") as mock_ctx:
            mock_ctx.verify.return_value = False
            result = verify_password("wrong", "$2b$12$hashed")
            assert result is False


class TestCreateAccessToken:
    def test_creates_valid_jwt(self):
        token = create_access_token(data={"sub": "user123", "tenant_id": "tenant1"})
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["sub"] == "user123"
        assert payload["tenant_id"] == "tenant1"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_custom_expiry(self):
        token = create_access_token(data={"sub": "user123"}, expires_minutes=5)
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
        diff = (exp - now).total_seconds()
        assert 290 < diff < 310

    def test_default_expiry(self):
        token = create_access_token(data={"sub": "user123"})
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
        diff_minutes = (exp - now).total_seconds() / 60
        assert 59 < diff_minutes < 61


class TestCreateRefreshToken:
    def test_creates_valid_jwt(self):
        token = create_refresh_token(data={"sub": "user123"})
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        assert payload["sub"] == "user123"
        assert payload["type"] == "refresh"
        assert "refresh_version" in payload
        assert payload["jti"] == payload["refresh_version"]
        assert "exp" in payload
        assert "iat" in payload

    def test_custom_expiry(self):
        token = create_refresh_token(data={"sub": "user123"}, expires_days=1)
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
        diff_days = (exp - now).total_seconds() / 86400
        assert 0.99 < diff_days < 1.01


class TestDecodeToken:
    def test_valid_token(self):
        token = create_access_token(data={"sub": "user123"})
        payload = decode_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"

    def test_invalid_token(self):
        result = decode_token("invalid.token.here")
        assert result is None

    def test_garbage_string(self):
        result = decode_token("not-a-jwt")
        assert result is None


class TestAddTokenToBlacklist:
    async def test_valid_token(self):
        token = create_access_token(data={"sub": "user123"})
        with patch("app.core.security.redis_set", new_callable=AsyncMock) as mock_set:
            await add_token_to_blacklist(token)
            mock_set.assert_called_once()
            call_args = mock_set.call_args
            assert "token_blacklist:" in call_args[0][0]
            assert call_args[0][1] == "1"

    async def test_invalid_token_early_return(self):
        with patch("app.core.security.redis_set", new_callable=AsyncMock) as mock_set:
            await add_token_to_blacklist("invalid-token")
            mock_set.assert_not_called()

    async def test_no_jti_early_return(self):
        token = jwt.encode(
            {"sub": "user", "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with patch("app.core.security.redis_set", new_callable=AsyncMock) as mock_set:
            await add_token_to_blacklist(token)
            mock_set.assert_not_called()

    async def test_custom_ttl(self):
        token = create_access_token(data={"sub": "user123"})
        with patch("app.core.security.redis_set", new_callable=AsyncMock) as mock_set:
            await add_token_to_blacklist(token, ttl_seconds=3600)
            call_args = mock_set.call_args
            assert call_args[1]["ex"] == 3600


class TestIsTokenBlacklisted:
    async def test_not_blacklisted(self):
        token = create_access_token(data={"sub": "user123"})
        with patch("app.core.security.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = await is_token_blacklisted(token)
            assert result is False

    async def test_blacklisted(self):
        token = create_access_token(data={"sub": "user123"})
        with patch("app.core.security.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "1"
            result = await is_token_blacklisted(token)
            assert result is True

    async def test_invalid_token_returns_false(self):
        with patch("app.core.security.redis_get", new_callable=AsyncMock) as mock_get:
            result = await is_token_blacklisted("invalid-token")
            assert result is False
            mock_get.assert_not_called()

    async def test_no_jti_returns_false(self):
        token = jwt.encode(
            {"sub": "user", "type": "access", "exp": datetime.now(UTC) + timedelta(hours=1)},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with patch("app.core.security.redis_get", new_callable=AsyncMock) as mock_get:
            result = await is_token_blacklisted(token)
            assert result is False
            mock_get.assert_not_called()


class TestBlacklistRefreshToken:
    async def test_calls_add_token_to_blacklist(self):
        token = create_refresh_token(data={"sub": "user123"})
        with patch("app.core.security.add_token_to_blacklist", new_callable=AsyncMock) as mock_add:
            await blacklist_refresh_token(token)
            mock_add.assert_called_once_with(token)


class TestIsRefreshTokenBlacklisted:
    async def test_not_blacklisted(self):
        with patch("app.core.security.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = None
            result = await is_refresh_token_blacklisted("refresh-version-123")
            assert result is False

    async def test_blacklisted(self):
        with patch("app.core.security.redis_get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = "1"
            result = await is_refresh_token_blacklisted("refresh-version-123")
            assert result is True


class TestExtractUserFromToken:
    def test_valid_access_token(self):
        token = create_access_token(
            data={
                "sub": "user123",
                "tenant_id": "tenant1",
                "role": "admin",
                "provider": "google",
            }
        )
        user_info = extract_user_from_token(token)
        assert user_info is not None
        assert user_info["user_id"] == "user123"
        assert user_info["tenant_id"] == "tenant1"
        assert user_info["role"] == "admin"
        assert user_info["provider"] == "google"

    def test_invalid_token_returns_none(self):
        result = extract_user_from_token("invalid-token")
        assert result is None

    def test_refresh_token_returns_none(self):
        token = create_refresh_token(data={"sub": "user123"})
        result = extract_user_from_token(token)
        assert result is None


class TestGetAccessTokenRemainingSeconds:
    def test_valid_token(self):
        token = create_access_token(data={"sub": "user123"}, expires_minutes=10)
        remaining = get_access_token_remaining_seconds(token)
        assert 590 < remaining < 610

    def test_invalid_token(self):
        result = get_access_token_remaining_seconds("invalid-token")
        assert result == 0

    def test_expired_token_returns_zero(self):
        token = create_access_token(data={"sub": "user123"}, expires_minutes=-1)
        result = get_access_token_remaining_seconds(token)
        assert result == 0
