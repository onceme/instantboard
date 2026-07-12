from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.core.exceptions import AuthRequired, Forbidden, InvalidToken
from app.dependencies import (
    _raw_token_var,
    get_current_tenant,
    get_current_user,
    get_db,
    get_optional_token,
    get_raw_token,
    get_redis,
    require_admin,
)
from app.core.security import create_access_token


class TestGetRawToken:
    def test_returns_none_by_default(self):
        token = _raw_token_var.set(None)
        result = get_raw_token()
        assert result is None

    def test_returns_set_value(self):
        _raw_token_var.set("test_token_value")
        result = get_raw_token()
        assert result == "test_token_value"


class TestGetCurrentUser:
    async def test_no_credentials_raises_auth_required(self):
        with patch("app.dependencies.get_redis", new_callable=AsyncMock):
            with pytest.raises(AuthRequired):
                async_gen = get_current_user(credentials=None, redis=MagicMock())
                await async_gen

    async def test_blacklisted_token_raises_invalid(self):
        token = create_access_token({"sub": "user123"})
        credentials = HTTPAuthorizationCredentials(
            scheme="bearer", credentials=token
        )
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = True
            with pytest.raises(InvalidToken, match="Token has been revoked"):
                await get_current_user(credentials=credentials, redis=MagicMock())

    async def test_invalid_token_payload_raises_invalid(self):
        credentials = HTTPAuthorizationCredentials(
            scheme="bearer", credentials="invalid_token"
        )
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            with pytest.raises(InvalidToken):
                await get_current_user(credentials=credentials, redis=MagicMock())

    async def test_no_user_id_raises_invalid(self):
        from jose import jwt
        from app.config import settings
        token = jwt.encode(
            {"type": "access", "tenant_id": "t1"},
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        credentials = HTTPAuthorizationCredentials(
            scheme="bearer", credentials=token
        )
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            with pytest.raises(InvalidToken, match="missing user_id"):
                await get_current_user(credentials=credentials, redis=MagicMock())

    async def test_valid_token_returns_user_info(self):
        token = create_access_token(
            data={"sub": "user123", "tenant_id": "tenant1", "role": "admin", "provider": "google"}
        )
        credentials = HTTPAuthorizationCredentials(
            scheme="bearer", credentials=token
        )
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            result = await get_current_user(credentials=credentials, redis=MagicMock())
            assert result["user_id"] == "user123"
            assert result["tenant_id"] == "tenant1"
            assert result["role"] == "admin"


class TestGetCurrentTenant:
    async def test_no_tenant_id_raises_auth_required(self):
        user = {"user_id": "u1", "tenant_id": None}
        with pytest.raises(AuthRequired, match="missing tenant_id"):
            await get_current_tenant(user=user)

    async def test_valid_tenant_id(self):
        user = {"user_id": "u1", "tenant_id": "tenant1"}
        result = await get_current_tenant(user=user)
        assert result == "tenant1"


class TestRequireAdmin:
    async def test_non_admin_raises_forbidden(self):
        user = {"user_id": "u1", "role": "user"}
        with pytest.raises(Forbidden, match="Admin role required"):
            await require_admin(user=user)

    async def test_admin_returns_user(self):
        user = {"user_id": "u1", "role": "admin"}
        result = await require_admin(user=user)
        assert result == user
        assert result["role"] == "admin"


class TestGetOptionalToken:
    async def test_query_token(self):
        token = create_access_token(data={"sub": "user123", "tenant_id": "t1"})
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            result = await get_optional_token(
                token=token, credentials=None, redis=MagicMock()
            )
            assert result is not None
            assert result["user_id"] == "user123"

    async def test_bearer_token(self):
        token = create_access_token(data={"sub": "user123", "tenant_id": "t1"})
        credentials = HTTPAuthorizationCredentials(
            scheme="bearer", credentials=token
        )
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            result = await get_optional_token(
                token=None, credentials=credentials, redis=MagicMock()
            )
            assert result is not None
            assert result["user_id"] == "user123"

    async def test_no_token_returns_none(self):
        result = await get_optional_token(
            token=None, credentials=None, redis=MagicMock()
        )
        assert result is None

    async def test_blacklisted_query_token_raises_invalid(self):
        token = create_access_token(data={"sub": "user123", "tenant_id": "t1"})
        
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = True
            with pytest.raises(InvalidToken, match="revoked"):
                await get_optional_token(
                    token=token, credentials=None, redis=MagicMock()
                )

    async def test_invalid_token_raises_invalid(self):
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            with pytest.raises(InvalidToken):
                await get_optional_token(
                    token="invalid_token", credentials=None, redis=MagicMock()
                )
