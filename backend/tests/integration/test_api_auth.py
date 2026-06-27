"""Tests for /api/v1/auth endpoints."""
import uuid
from unittest.mock import patch, AsyncMock

from app.core.security import create_access_token, create_refresh_token

from tests.integration.conftest import make_auth_header, make_admin_headers


class TestSSOAuthorize:
    def test_valid_provider_github(self, client):
        resp = client.get(
            "/api/v1/auth/sso/github/authorize",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "authorize_url" in data["data"]
        assert "state" in data["data"]

    def test_valid_provider_google(self, client):
        resp = client.get(
            "/api/v1/auth/sso/google/authorize",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 200

    def test_invalid_provider(self, client):
        resp = client.get(
            "/api/v1/auth/sso/slack/authorize",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "Unsupported SSO provider" in data["detail"]["error"]["message"]

    def test_missing_redirect_uri(self, client):
        resp = client.get("/api/v1/auth/sso/github/authorize")
        assert resp.status_code == 422


class TestSSOLogin:
    def test_invalid_provider_login(self, client):
        resp = client.post(
            "/api/v1/auth/sso/slack",
            json={"code": "abc123", "redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400

    @patch("app.api.v1.auth.AuthService")
    def test_valid_provider_login(self, mock_auth_cls, client):
        mock_service = AsyncMock()
        mock_service.sso_login.return_value = {
            "access_token": "fake-access",
            "refresh_token": "fake-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "user": {
                "id": str(uuid.uuid4()),
                "email": "test@example.com",
                "name": "Test",
                "avatar_url": None,
                "tenant_id": str(uuid.uuid4()),
                "role": "user",
                "sso_provider": "github",
            },
        }
        mock_auth_cls.return_value = mock_service

        resp = client.post(
            "/api/v1/auth/sso/github",
            json={"code": "valid-code", "redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["access_token"] == "fake-access"

    def test_login_missing_body(self, client):
        resp = client.post("/api/v1/auth/sso/github", json={"redirect_uri": "x"})
        assert resp.status_code == 422


class TestRefreshToken:
    @patch("app.api.v1.auth.AuthService")
    def test_refresh_success(self, mock_auth_cls, client):
        mock_service = AsyncMock()
        mock_service.refresh_token.return_value = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }
        mock_auth_cls.return_value = mock_service

        resp = client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "some-refresh-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["data"]["access_token"] == "new-access"

    def test_refresh_missing_token(self, client):
        resp = client.post("/api/v1/auth/refresh", json={})
        assert resp.status_code == 422


class TestGetCurrentUser:
    def test_get_me_with_valid_token(self, client):
        headers, tenant_id, user_id = make_auth_header(role="user")
        from unittest.mock import patch, AsyncMock

        with patch("app.api.v1.auth.AuthService") as mock_cls:
            mock_service = AsyncMock()
            mock_service.get_current_user.return_value = {
                "id": user_id,
                "email": "user@example.com",
                "name": "Test User",
                "avatar_url": None,
                "tenant_id": tenant_id,
                "role": "user",
                "sso_provider": "github",
            }
            mock_cls.return_value = mock_service
            resp = client.get("/api/v1/auth/me", headers=headers)
            assert resp.status_code == 200
            assert resp.json()["data"]["email"] == "user@example.com"

    def test_get_me_without_token(self, client):
        resp = client.get("/api/v1/auth/me")
        assert resp.status_code == 401

    def test_get_me_invalid_token(self, client):
        headers = {"Authorization": "Bearer invalid-jwt-token"}
        resp = client.get("/api/v1/auth/me", headers=headers)
        assert resp.status_code == 401


class TestLogout:
    def test_logout_success(self, client):
        headers, tenant_id, user_id = make_auth_header()

        with patch("app.api.v1.auth.AuthService") as mock_cls:
            mock_service = AsyncMock()
            mock_service.logout.return_value = {"message": "Logged out"}
            mock_cls.return_value = mock_service

            resp = client.request("DELETE", "/api/v1/auth/logout", headers=headers, json={"refresh_token": "rt"})
            assert resp.status_code == 200
            assert resp.json()["data"]["message"] == "Logged out"

    def test_logout_no_auth(self, client):
        resp = client.request("DELETE", "/api/v1/auth/logout", json={})
        assert resp.status_code == 401
