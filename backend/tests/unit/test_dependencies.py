from contextvars import ContextVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.security import HTTPAuthorizationCredentials

from app.core.exceptions import AuthRequired, Forbidden, InvalidToken
from app.core.security import create_access_token
from app.dependencies import (
    _raw_token_var,
    get_client_ip,
    get_current_tenant,
    get_current_user,
    get_db,
    get_optional_token,
    get_raw_token,
    get_redis,
    require_admin,
)


def _make_request(headers: dict[str, str] | None = None, client_host: str | None = "203.0.113.9") -> Request:
    scope: dict = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/admin/login",
        "headers": [(name.lower().encode(), value.encode()) for name, value in (headers or {}).items()],
    }
    if client_host is not None:
        scope["client"] = (client_host, 54321)
    return Request(scope)


class TestGetRawToken:
    def test_returns_none_by_default(self):
        _raw_token_var.set(None)
        result = get_raw_token()
        assert result is None

    def test_returns_set_value(self):
        _raw_token_var.set("test_token_value")
        result = get_raw_token()
        assert result == "test_token_value"


class TestGetCurrentUser:
    async def test_no_credentials_raises_auth_required(self):
        with patch("app.dependencies.get_redis", new_callable=AsyncMock), pytest.raises(AuthRequired):
            async_gen = get_current_user(credentials=None, redis=MagicMock())
            await async_gen

    async def test_blacklisted_token_raises_invalid(self):
        token = create_access_token({"sub": "user123"})
        credentials = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)

        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = True
            with pytest.raises(InvalidToken, match="Token has been revoked"):
                await get_current_user(credentials=credentials, redis=MagicMock())

    async def test_invalid_token_payload_raises_invalid(self):
        credentials = HTTPAuthorizationCredentials(scheme="bearer", credentials="invalid_token")

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
        credentials = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)

        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            with pytest.raises(InvalidToken, match="missing user_id"):
                await get_current_user(credentials=credentials, redis=MagicMock())

    async def test_valid_token_returns_user_info(self):
        token = create_access_token(
            data={"sub": "user123", "tenant_id": "tenant1", "role": "admin", "provider": "google"}
        )
        credentials = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)

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
            result = await get_optional_token(token=token, credentials=None, redis=MagicMock())
            assert result is not None
            assert result["user_id"] == "user123"

    async def test_bearer_token(self):
        token = create_access_token(data={"sub": "user123", "tenant_id": "t1"})
        credentials = HTTPAuthorizationCredentials(scheme="bearer", credentials=token)

        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            result = await get_optional_token(token=None, credentials=credentials, redis=MagicMock())
            assert result is not None
            assert result["user_id"] == "user123"

    async def test_no_token_returns_none(self):
        result = await get_optional_token(token=None, credentials=None, redis=MagicMock())
        assert result is None

    async def test_blacklisted_query_token_raises_invalid(self):
        token = create_access_token(data={"sub": "user123", "tenant_id": "t1"})

        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = True
            with pytest.raises(InvalidToken, match="revoked"):
                await get_optional_token(token=token, credentials=None, redis=MagicMock())

    async def test_invalid_token_raises_invalid(self):
        with patch("app.dependencies.is_token_blacklisted", new_callable=AsyncMock) as mock_bl:
            mock_bl.return_value = False
            with pytest.raises(InvalidToken):
                await get_optional_token(token="invalid_token", credentials=None, redis=MagicMock())


class TestGetClientIp:
    """Rightmost-trusted-hop semantics: nginx appends the real peer via
    $proxy_add_x_forwarded_for, so the rightmost valid entry is the trusted one."""

    REAL_PEER = "203.0.113.9"
    SPOOFED = "198.51.100.1"

    def test_single_hop(self):
        request = _make_request(headers={"X-Forwarded-For": self.REAL_PEER})
        assert get_client_ip(request) == self.REAL_PEER

    def test_multiple_hops_returns_rightmost(self):
        # A client-forged leftmost hop must be ignored.
        request = _make_request(headers={"X-Forwarded-For": f"{self.SPOOFED}, {self.REAL_PEER}"})
        assert get_client_ip(request) == self.REAL_PEER

    def test_long_chain_returns_rightmost(self):
        request = _make_request(headers={"X-Forwarded-For": f"{self.SPOOFED}, 192.0.2.77, 192.0.2.1, {self.REAL_PEER}"})
        assert get_client_ip(request) == self.REAL_PEER

    def test_blank_segments_skipped(self):
        request = _make_request(headers={"X-Forwarded-For": f" , {self.SPOOFED},,{self.REAL_PEER}, ,"})
        assert get_client_ip(request) == self.REAL_PEER

    def test_rightmost_blank_falls_leftward(self):
        request = _make_request(headers={"X-Forwarded-For": f"{self.SPOOFED}, {self.REAL_PEER}, ,"})
        assert get_client_ip(request) == self.REAL_PEER

    def test_invalid_rightmost_ip_skipped(self):
        request = _make_request(headers={"X-Forwarded-For": f"{self.SPOOFED}, not-an-ip"})
        assert get_client_ip(request) == self.SPOOFED

    def test_all_hops_invalid_falls_back_to_socket_peer(self):
        request = _make_request(headers={"X-Forwarded-For": "garbage, 999.999.999.999"})
        assert get_client_ip(request) == self.REAL_PEER  # request.client fallback

    def test_only_blank_header_falls_back_to_socket_peer(self):
        request = _make_request(headers={"X-Forwarded-For": " , , "})
        assert get_client_ip(request) == self.REAL_PEER  # request.client fallback

    def test_no_header_uses_socket_peer(self):
        request = _make_request()
        assert get_client_ip(request) == self.REAL_PEER

    def test_no_header_no_socket_peer_returns_unknown(self):
        request = _make_request(client_host=None)
        assert get_client_ip(request) == "unknown"

    def test_header_present_but_no_socket_peer_returns_rightmost(self):
        request = _make_request(headers={"X-Forwarded-For": self.REAL_PEER}, client_host=None)
        assert get_client_ip(request) == self.REAL_PEER

    def test_ipv6_rightmost(self):
        request = _make_request(headers={"X-Forwarded-For": "192.0.2.1, 2001:db8::7"})
        assert get_client_ip(request) == "2001:db8::7"

    def test_single_entry_overwritten_by_nginx_auth_location(self):
        # /api/v1/auth/ rewrites XFF to exactly $remote_addr; semantics agree both ways.
        request = _make_request(headers={"X-Forwarded-For": self.REAL_PEER})
        assert get_client_ip(request) == self.REAL_PEER
