"""Tests for /api/v1/auth endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.security import create_access_token, create_refresh_token
from app.core.sso_handlers import SSOUserInfo
from tests.integration.conftest import make_admin_headers, make_auth_header

CALLBACK_URI = "http://localhost:3000/callback"


def _authorize(client, provider="github"):
    """Hit the real authorize endpoint so the issued state lands in the mock Redis."""
    resp = client.get(
        f"/api/v1/auth/sso/{provider}/authorize",
        params={"redirect_uri": CALLBACK_URI},
    )
    assert resp.status_code == 200
    return resp.json()["data"]["state"]


class TestSSOProviders:
    """Tests for the GET /api/v1/auth/sso/providers endpoint."""

    def test_get_enabled_providers_default(self, client):
        """Default configuration returns google and github only."""
        resp = client.get("/api/v1/auth/sso/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        providers = data["data"]["enabled_providers"]
        assert "google" in providers
        assert "github" in providers
        # azure_ad, apple, facebook are NOT enabled by default
        assert "azure_ad" not in providers
        assert "apple" not in providers
        assert "facebook" not in providers

    def test_get_enabled_providers_response_shape(self, client):
        """Response contains the expected structure."""
        resp = client.get("/api/v1/auth/sso/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert "enabled_providers" in data["data"]
        assert isinstance(data["data"]["enabled_providers"], list)

    def test_get_enabled_providers_custom(self, client):
        """With custom enabled_sso_providers config, the returned list reflects the config."""
        with patch("app.api.v1.auth.settings") as mock_settings:
            mock_settings.enabled_sso_providers = ["google", "github", "azure_ad"]
            resp = client.get("/api/v1/auth/sso/providers")
            assert resp.status_code == 200
            providers = resp.json()["data"]["enabled_providers"]
            assert "google" in providers
            assert "github" in providers
            assert "azure_ad" in providers

    def test_get_enabled_providers_empty_config(self, client):
        """With an empty enabled list, the returned list is empty."""
        with patch("app.api.v1.auth.settings") as mock_settings:
            mock_settings.enabled_sso_providers = []
            resp = client.get("/api/v1/auth/sso/providers")
            assert resp.status_code == 200
            providers = resp.json()["data"]["enabled_providers"]
            assert providers == []


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

    def test_disabled_provider_azure_ad_returns_400(self, client):
        """azure_ad is supported but not enabled by default; authorize must return 400."""
        resp = client.get(
            "/api/v1/auth/sso/azure_ad/authorize",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "not enabled" in data["detail"]["error"]["message"]

    def test_disabled_provider_apple_returns_400(self, client):
        """apple is supported but not enabled by default; authorize must return 400."""
        resp = client.get(
            "/api/v1/auth/sso/apple/authorize",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "not enabled" in data["detail"]["error"]["message"]

    def test_disabled_provider_facebook_returns_400(self, client):
        """facebook is supported but not enabled by default; authorize must return 400."""
        resp = client.get(
            "/api/v1/auth/sso/facebook/authorize",
            params={"redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "not enabled" in data["detail"]["error"]["message"]

    def test_authorize_stores_state_in_redis(self, client, app_with_overrides):
        """OAuth CSRF protection (security.md §3.2): the issued state is stored in Redis
        under sso_state:{state} with the provider as value and a 10-minute TTL."""
        from app.core.redis import RedisKeys

        _, mock_redis = app_with_overrides
        state = _authorize(client, "github")
        key = RedisKeys.sso_state_key(state)
        assert mock_redis._data.get(key) == "github"
        assert key in mock_redis._expiry
        assert mock_redis._expiry[key] - mock_redis._clock == RedisKeys.SSO_STATE_TTL == 600

    def test_authorize_redis_failure_fails_open(self, client, app_with_overrides, monkeypatch):
        """Redis down at authorize time degrades to fail-open (warning log): the
        authorize request still succeeds and issues a state (security.md §3.2)."""
        _, mock_redis = app_with_overrides

        async def broken_set(*args, **kwargs):
            raise ConnectionError("redis down")

        monkeypatch.setattr(mock_redis, "set", broken_set)
        resp = client.get(
            "/api/v1/auth/sso/github/authorize",
            params={"redirect_uri": CALLBACK_URI},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["state"]
        assert data["authorize_url"]


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

    def test_disabled_provider_login_returns_400(self, client):
        """Attempting to login with a supported but disabled provider must return 400."""
        # azure_ad is supported but not in the default enabled list
        resp = client.post(
            "/api/v1/auth/sso/azure_ad",
            json={"code": "abc123", "redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "not enabled" in data["detail"]["error"]["message"]

    def test_disabled_apple_provider_login_returns_400(self, client):
        """Apple is supported but not enabled by default; login must return 400."""
        resp = client.post(
            "/api/v1/auth/sso/apple",
            json={"code": "abc123", "redirect_uri": "http://localhost:3000/callback"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "not enabled" in data["detail"]["error"]["message"]

    def test_google_login_email_unverified_returns_400(self, client):
        """Google SSO login with an unverified email (email_verified=False) must be
        rejected with 400 VALIDATION_ERROR before any user is provisioned
        (security.md §3.4)."""
        handler = AsyncMock()
        handler.authenticate = AsyncMock(
            return_value=SSOUserInfo(
                provider="google",
                provider_id=f"google-unverified-{uuid.uuid4().hex[:10]}",
                email=f"unverified-{uuid.uuid4().hex[:8]}@gmail.com",
                name="Unverified User",
                email_verified=False,
            )
        )
        handler.close = AsyncMock()

        state = _authorize(client, "google")
        with patch("app.services.auth.SSOHandlerFactory") as mock_factory:
            mock_factory.create.return_value = handler
            resp = client.post(
                "/api/v1/auth/sso/google",
                json={"code": "code123", "redirect_uri": CALLBACK_URI, "state": state},
            )

        assert resp.status_code == 400
        data = resp.json()
        assert data["detail"]["success"] is False
        assert data["detail"]["error"]["code"] == "VALIDATION_ERROR"
        assert data["detail"]["error"]["message"] == "Google account email is not verified"

    def test_google_login_email_verified_returns_200(self, client):
        """Google SSO login with a verified email proceeds end-to-end and provisions
        the user."""
        provider_id = f"google-verified-{uuid.uuid4().hex[:10]}"
        handler = AsyncMock()
        handler.authenticate = AsyncMock(
            return_value=SSOUserInfo(
                provider="google",
                provider_id=provider_id,
                email=f"verified-{uuid.uuid4().hex[:8]}@gmail.com",
                name="Verified User",
                email_verified=True,
            )
        )
        handler.close = AsyncMock()

        state = _authorize(client, "google")
        with patch("app.services.auth.SSOHandlerFactory") as mock_factory:
            mock_factory.create.return_value = handler
            resp = client.post(
                "/api/v1/auth/sso/google",
                json={"code": "code123", "redirect_uri": CALLBACK_URI, "state": state},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["access_token"]
        assert data["data"]["user"]["sso_provider"] == "google"
        assert data["data"]["user"]["role"] == "member"


class TestSSOLoginState:
    """OAuth state verification on SSO login (security.md §3.2): the state issued by
    the authorize endpoint is mandatory, provider-bound, single-use, and TTL-limited."""

    def _mock_github_handler(self):
        handler = AsyncMock()
        handler.authenticate = AsyncMock(
            return_value=SSOUserInfo(
                provider="github",
                provider_id=f"gh-{uuid.uuid4().hex[:10]}",
                email=f"state-user-{uuid.uuid4().hex[:8]}@example.com",
                name="State User",
            )
        )
        handler.close = AsyncMock()
        return handler

    def test_valid_state_consumed_and_reuse_rejected(self, client, app_with_overrides):
        """A full authorize→login round-trip succeeds and consumes the state; a second
        login with the same state is rejected (single-use)."""
        from app.core.redis import RedisKeys

        _, mock_redis = app_with_overrides
        state = _authorize(client, "github")
        body = {"code": "valid-code", "redirect_uri": CALLBACK_URI, "state": state}

        with patch("app.services.auth.SSOHandlerFactory") as mock_factory:
            mock_factory.create.return_value = self._mock_github_handler()
            resp = client.post("/api/v1/auth/sso/github", json=body)

        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # Single-use: the key is gone immediately after the successful login.
        assert RedisKeys.sso_state_key(state) not in mock_redis._data

        resp2 = client.post("/api/v1/auth/sso/github", json=body)
        assert resp2.status_code == 400
        assert resp2.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"
        assert resp2.json()["detail"]["error"]["message"] == "Invalid or expired OAuth state"

    def test_missing_state_returns_400(self, client):
        resp = client.post(
            "/api/v1/auth/sso/github",
            json={"code": "valid-code", "redirect_uri": CALLBACK_URI},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["message"] == "Invalid or expired OAuth state"

    def test_unknown_state_returns_400(self, client):
        resp = client.post(
            "/api/v1/auth/sso/github",
            json={"code": "valid-code", "redirect_uri": CALLBACK_URI, "state": "never-issued-state"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["message"] == "Invalid or expired OAuth state"

    def test_expired_state_returns_400(self, client, app_with_overrides):
        """States expire after SSO_STATE_TTL (600s); login is rejected afterwards."""
        _, mock_redis = app_with_overrides
        state = _authorize(client, "github")
        mock_redis.advance(601)

        resp = client.post(
            "/api/v1/auth/sso/github",
            json={"code": "valid-code", "redirect_uri": CALLBACK_URI, "state": state},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["message"] == "Invalid or expired OAuth state"

    def test_state_provider_mismatch_returns_400(self, client):
        """A state issued for github cannot be used on the google login endpoint."""
        state = _authorize(client, "github")
        resp = client.post(
            "/api/v1/auth/sso/google",
            json={"code": "valid-code", "redirect_uri": CALLBACK_URI, "state": state},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["message"] == "Invalid or expired OAuth state"

    def test_disabled_provider_still_rejected_with_valid_state(self, client):
        """The provider enabled-check runs before state validation, so the disabled
        provider contract (400 'not enabled') is unaffected by the state checks."""
        # authorize for azure_ad already fails (disabled), so no state can be issued;
        # posting any state still surfaces the provider error first.
        resp = client.post(
            "/api/v1/auth/sso/azure_ad",
            json={"code": "valid-code", "redirect_uri": CALLBACK_URI, "state": "whatever-state"},
        )
        assert resp.status_code == 400
        assert "not enabled" in resp.json()["detail"]["error"]["message"]


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
        from unittest.mock import AsyncMock, patch

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
