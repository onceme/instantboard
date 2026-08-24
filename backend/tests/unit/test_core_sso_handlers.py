import time
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import httpx
import pytest
from jose import jwt

from app.core.sso_handlers import (
    SUPPORTED_PROVIDERS,
    AppleSSOHandler,
    AzureADSSOHandler,
    BaseSSOHandler,
    FacebookSSOHandler,
    GitHubSSOHandler,
    GoogleSSOHandler,
    SSOHandlerFactory,
    SSOTokenResponse,
    SSOUserInfo,
)


def create_mock_settings(enabled_providers: list[str] | None = None) -> MagicMock:
    """Create a mock settings object with specified enabled SSO providers."""
    if enabled_providers is None:
        enabled_providers = ["google", "github"]
    mock_settings = MagicMock()
    mock_settings.enabled_sso_providers = enabled_providers
    return mock_settings


def _make_mock_client():
    mock_client = MagicMock(spec=httpx.AsyncClient)
    mock_client.is_closed = False
    mock_client.post = AsyncMock()
    mock_client.get = AsyncMock()
    mock_client.aclose = AsyncMock()
    return mock_client


class TestSSOUserInfo:
    def test_init_all_fields(self):
        info = SSOUserInfo(
            provider="google",
            provider_id="123",
            email="test@example.com",
            name="Test User",
            avatar_url="http://img.png",
            email_verified=True,
            raw_data={"key": "val"},
        )
        assert info.provider == "google"
        assert info.provider_id == "123"
        assert info.email == "test@example.com"
        assert info.name == "Test User"
        assert info.avatar_url == "http://img.png"
        assert info.email_verified is True
        assert info.raw_data == {"key": "val"}

    def test_init_defaults(self):
        info = SSOUserInfo(provider="github", provider_id="456")
        assert info.email is None
        assert info.name is None
        assert info.avatar_url is None
        assert info.email_verified is False
        assert info.raw_data == {}

    def test_email_verified_default(self):
        info = SSOUserInfo(provider="google", provider_id="1", email_verified=False)
        assert info.email_verified is False


class TestSSOTokenResponse:
    def test_init(self):
        resp = SSOTokenResponse(access_token="at123", id_token="it456")
        assert resp.access_token == "at123"
        assert resp.id_token == "it456"

    def test_init_no_id_token(self):
        resp = SSOTokenResponse(access_token="at123")
        assert resp.id_token is None


class TestBaseSSOHandler:
    async def test_get_http_client_creates_new(self):
        class DummyHandler(BaseSSOHandler):
            def get_authorize_url(self, state, redirect_uri):
                return ""

            async def exchange_code(self, code, redirect_uri):
                return SSOTokenResponse("t")

            async def get_user_info(self, token_response):
                return SSOUserInfo("x", "1")

        handler = DummyHandler()
        client = await handler._get_http_client()
        assert isinstance(client, httpx.AsyncClient)
        await handler.close()

    async def test_get_http_client_reuses(self):
        class DummyHandler(BaseSSOHandler):
            def get_authorize_url(self, state, redirect_uri):
                return ""

            async def exchange_code(self, code, redirect_uri):
                return SSOTokenResponse("t")

            async def get_user_info(self, token_response):
                return SSOUserInfo("x", "1")

        handler = DummyHandler()
        c1 = await handler._get_http_client()
        c2 = await handler._get_http_client()
        assert c1 is c2
        await handler.close()

    async def test_close(self):
        class DummyHandler(BaseSSOHandler):
            def get_authorize_url(self, state, redirect_uri):
                return ""

            async def exchange_code(self, code, redirect_uri):
                return SSOTokenResponse("t")

            async def get_user_info(self, token_response):
                return SSOUserInfo("x", "1")

        handler = DummyHandler()
        await handler._get_http_client()
        await handler.close()
        assert handler._http_client is None or handler._http_client.is_closed

    async def test_authenticate(self):
        handler = GoogleSSOHandler()
        handler.exchange_code = AsyncMock(return_value=SSOTokenResponse("token"))
        handler.get_user_info = AsyncMock(return_value=SSOUserInfo("google", "123"))
        user_info = await handler.authenticate("code", "http://localhost/cb")
        assert user_info.provider == "google"
        handler.exchange_code.assert_called_once_with("code", "http://localhost/cb")

    async def test_close_when_no_client(self):
        class DummyHandler(BaseSSOHandler):
            def get_authorize_url(self, state, redirect_uri):
                return ""

            async def exchange_code(self, code, redirect_uri):
                return SSOTokenResponse("t")

            async def get_user_info(self, token_response):
                return SSOUserInfo("x", "1")

        handler = DummyHandler()
        await handler.close()


class TestSSOHandlerFactory:
    def test_create_google(self):
        handler = SSOHandlerFactory.create("google")
        assert isinstance(handler, GoogleSSOHandler)

    def test_create_azure_ad(self):
        handler = SSOHandlerFactory.create("azure_ad")
        assert isinstance(handler, AzureADSSOHandler)

    def test_create_github(self):
        handler = SSOHandlerFactory.create("github")
        assert isinstance(handler, GitHubSSOHandler)

    def test_create_apple(self):
        handler = SSOHandlerFactory.create("apple")
        assert isinstance(handler, AppleSSOHandler)

    def test_create_facebook(self):
        handler = SSOHandlerFactory.create("facebook")
        assert isinstance(handler, FacebookSSOHandler)

    def test_invalid_provider(self):
        with pytest.raises(ValueError, match="Unsupported SSO provider"):
            SSOHandlerFactory.create("invalid_provider")

    def test_get_supported_providers(self):
        providers = SSOHandlerFactory.get_supported_providers()
        assert len(providers) == 5
        assert "google" in providers
        assert "azure_ad" in providers
        assert "github" in providers
        assert "apple" in providers
        assert "facebook" in providers

    # -- New tests for settings-aware create() --

    def test_create_with_settings_enabled_provider(self):
        """Creating a handler for an enabled provider should succeed."""
        mock_settings = create_mock_settings(["google", "github"])
        handler = SSOHandlerFactory.create("google", settings=mock_settings)
        assert isinstance(handler, GoogleSSOHandler)

    def test_create_with_settings_disabled_provider_raises(self):
        """Creating a handler for a supported but disabled provider should raise ValueError."""
        mock_settings = create_mock_settings(["google", "github"])
        with pytest.raises(ValueError, match="SSO provider 'apple' is not enabled"):
            SSOHandlerFactory.create("apple", settings=mock_settings)

    def test_create_with_settings_unsupported_provider_raises(self):
        """Unsupported provider raises ValueError even if listed in enabled providers config."""
        mock_settings = create_mock_settings(["google", "github", "totally_invalid"])
        with pytest.raises(ValueError, match="SSO provider 'totally_invalid' is not enabled"):
            SSOHandlerFactory.create("totally_invalid", settings=mock_settings)

    def test_create_without_settings_allows_any_supported(self):
        """Without settings, create() allows any supported provider (legacy behavior)."""
        handler = SSOHandlerFactory.create("apple")
        assert isinstance(handler, AppleSSOHandler)

    def test_create_with_settings_empty_enabled_list(self):
        """With an empty enabled list, all supported providers should fail."""
        mock_settings = create_mock_settings([])
        for provider in SSOHandlerFactory.get_supported_providers():
            with pytest.raises(ValueError, match="is not enabled"):
                SSOHandlerFactory.create(provider, settings=mock_settings)

    # -- Tests for get_enabled_providers() --

    def test_get_enabled_providers_default_config(self):
        """Default config (google + github) returns exactly those two providers."""
        mock_settings = create_mock_settings(["google", "github"])
        result = SSOHandlerFactory.get_enabled_providers(mock_settings)
        assert result == ["google", "github"]

    def test_get_enabled_providers_with_azure_ad(self):
        """Config including azure_ad returns google, github, and azure_ad."""
        mock_settings = create_mock_settings(["google", "github", "azure_ad"])
        result = SSOHandlerFactory.get_enabled_providers(mock_settings)
        assert "google" in result
        assert "github" in result
        assert "azure_ad" in result
        assert len(result) == 3

    def test_get_enabled_providers_all_providers(self):
        """Config with all supported providers returns all five providers."""
        all_providers = list(SUPPORTED_PROVIDERS)
        mock_settings = create_mock_settings(all_providers)
        result = SSOHandlerFactory.get_enabled_providers(mock_settings)
        assert set(result) == set(all_providers)
        assert len(result) == len(all_providers)

    def test_get_enabled_providers_empty_config(self):
        """Empty config returns an empty list."""
        mock_settings = create_mock_settings([])
        result = SSOHandlerFactory.get_enabled_providers(mock_settings)
        assert result == []

    def test_get_enabled_providers_filters_invalid_names(self):
        """Invalid provider names in config are silently filtered out."""
        mock_settings = create_mock_settings(["google", "invalid_xyz", "github", "bogus"])
        result = SSOHandlerFactory.get_enabled_providers(mock_settings)
        assert result == ["google", "github"]
        assert "invalid_xyz" not in result
        assert "bogus" not in result

    def test_get_enabled_providers_preserves_order(self):
        """Returned providers preserve the order from the config."""
        mock_settings = create_mock_settings(["facebook", "google", "azure_ad"])
        result = SSOHandlerFactory.get_enabled_providers(mock_settings)
        assert result == ["facebook", "google", "azure_ad"]


class TestGoogleSSOHandler:
    def test_get_authorize_url(self):
        handler = GoogleSSOHandler()
        url = handler.get_authorize_url("state123", "http://localhost/callback")
        assert "accounts.google.com" in url
        assert "state=state123" in url
        assert "redirect_uri=http" in url
        assert "response_type=code" in url
        assert "scope=openid" in url
        assert "access_type=offline" in url

    async def test_exchange_code_success(self):
        handler = GoogleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at123", "id_token": "it456"}

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        result = await handler.exchange_code("code", "http://localhost/cb")
        assert result.access_token == "at123"
        assert result.id_token == "it456"

    async def test_exchange_code_failure(self):
        handler = GoogleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Google token exchange failed"):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_get_user_info_success(self):
        handler = GoogleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "sub": "user123",
            "email": "test@gmail.com",
            "name": "Test",
            "picture": "http://pic.png",
            "email_verified": True,
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        token_resp = SSOTokenResponse("at123")
        user_info = await handler.get_user_info(token_resp)
        assert user_info.provider == "google"
        assert user_info.provider_id == "user123"
        assert user_info.email == "test@gmail.com"
        assert user_info.email_verified is True

    async def test_get_user_info_failure(self):
        handler = GoogleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Google userinfo request failed"):
            await handler.get_user_info(SSOTokenResponse("at123"))


class TestAzureADSSOHandler:
    def test_get_authorize_url(self):
        handler = AzureADSSOHandler()
        url = handler.get_authorize_url("state123", "http://localhost/callback")
        assert "login.microsoftonline.com" in url
        assert "state=state123" in url
        assert "response_type=code" in url
        assert "response_mode=query" in url

    async def test_exchange_code_success(self):
        handler = AzureADSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at123", "id_token": "it456"}

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        result = await handler.exchange_code("code", "http://localhost/cb")
        assert result.access_token == "at123"

    async def test_exchange_code_failure(self):
        handler = AzureADSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Azure AD token exchange failed"):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_get_user_info_success(self):
        handler = AzureADSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "user123",
            "mail": "test@company.com",
            "displayName": "Test User",
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("at123"))
        assert user_info.provider == "azure_ad"
        assert user_info.provider_id == "user123"
        assert user_info.email == "test@company.com"
        assert user_info.email_verified is True

    async def test_get_user_info_with_upn_fallback(self):
        handler = AzureADSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "user123",
            "userPrincipalName": "test@company.com",
            "displayName": "Test User",
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("at123"))
        assert user_info.email == "test@company.com"

    async def test_get_user_info_failure(self):
        handler = AzureADSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Azure AD userinfo request failed"):
            await handler.get_user_info(SSOTokenResponse("at123"))


class TestGitHubSSOHandler:
    def test_get_authorize_url(self):
        handler = GitHubSSOHandler()
        url = handler.get_authorize_url("state123", "http://localhost/callback")
        assert "github.com/login/oauth" in url
        assert "state=state123" in url
        assert "scope=user" in url

    async def test_exchange_code_success(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "ghtoken"}

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        result = await handler.exchange_code("code", "http://localhost/cb")
        assert result.access_token == "ghtoken"

    async def test_exchange_code_failure(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="GitHub token exchange failed"):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_exchange_code_error_in_response(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "bad_verification_code", "error_description": "The code is bad"}

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="GitHub token exchange error"):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_get_user_info_with_email(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": 123,
            "login": "testuser",
            "name": "Test User",
            "email": "test@github.com",
            "avatar_url": "http://avatar.png",
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("ghtoken"))
        assert user_info.provider == "github"
        assert user_info.provider_id == "123"
        assert user_info.email == "test@github.com"
        assert user_info.name == "Test User"
        assert user_info.avatar_url == "http://avatar.png"

    async def test_get_user_info_no_email_fallback_to_emails(self):
        handler = GitHubSSOHandler()

        mock_user_response = MagicMock()
        mock_user_response.status_code = 200
        mock_user_response.json.return_value = {
            "id": 123,
            "login": "testuser",
            "name": None,
            "email": None,
            "avatar_url": "http://avatar.png",
        }

        mock_emails_response = MagicMock()
        mock_emails_response.status_code = 200
        mock_emails_response.json.return_value = [
            {"email": "primary@github.com", "primary": True, "verified": True},
            {"email": "other@github.com", "primary": False, "verified": True},
        ]

        mock_client = _make_mock_client()
        mock_client.get.side_effect = [mock_user_response, mock_emails_response]
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("ghtoken"))
        assert user_info.email == "primary@github.com"
        assert user_info.email_verified is True
        assert user_info.name == "testuser"

    async def test_get_user_info_failure(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="GitHub userinfo request failed"):
            await handler.get_user_info(SSOTokenResponse("ghtoken"))

    async def test_fetch_primary_email_failure(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response

        email, verified = await handler._fetch_primary_email(mock_client, {})
        assert email is None
        assert verified is False

    async def test_fetch_primary_email_no_primary_uses_verified(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"email": "verified@github.com", "primary": False, "verified": True},
        ]

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response

        email, verified = await handler._fetch_primary_email(mock_client, {})
        assert email == "verified@github.com"
        assert verified is True

    async def test_fetch_primary_email_no_verified(self):
        handler = GitHubSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"email": "unverified@github.com", "primary": False, "verified": False},
        ]

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response

        email, verified = await handler._fetch_primary_email(mock_client, {})
        assert email is None
        assert verified is False


class TestAppleSSOHandler:
    def test_get_authorize_url(self):
        handler = AppleSSOHandler()
        url = handler.get_authorize_url("state123", "http://localhost/callback")
        assert "appleid.apple.com" in url
        assert "state=state123" in url
        assert "response_mode=form_post" in url
        assert "scope=email" in url

    def test_generate_client_secret_incomplete_config(self):
        handler = AppleSSOHandler()
        with pytest.raises(ValueError, match="Apple SSO configuration incomplete"):
            handler._generate_client_secret()

    async def test_exchange_code_success(self):
        handler = AppleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "at123", "id_token": "it456"}

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with patch.object(handler, "_generate_client_secret", return_value="secret"):
            result = await handler.exchange_code("code", "http://localhost/cb")
            assert result.access_token == "at123"
            assert result.id_token == "it456"

    async def test_exchange_code_failure(self):
        handler = AppleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with (
            patch.object(handler, "_generate_client_secret", return_value="secret"),
            pytest.raises(ValueError, match="Apple token exchange failed"),
        ):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_exchange_code_error_in_response(self):
        handler = AppleSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "invalid_grant", "error_description": "bad grant"}

        mock_client = _make_mock_client()
        mock_client.post.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with (
            patch.object(handler, "_generate_client_secret", return_value="secret"),
            pytest.raises(ValueError, match="Apple token exchange error"),
        ):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_get_user_info_success(self):
        handler = AppleSSOHandler()
        claims_data = {
            "sub": "apple_user_123",
            "email": "test@icloud.com",
            "email_verified": "true",
        }
        id_token = jwt.encode(claims_data, "secret", algorithm="HS256")

        user_info = await handler.get_user_info(SSOTokenResponse("at123", id_token))
        assert user_info.provider == "apple"
        assert user_info.provider_id == "apple_user_123"
        assert user_info.email == "test@icloud.com"

    async def test_get_user_info_no_id_token(self):
        handler = AppleSSOHandler()
        with pytest.raises(ValueError, match="Apple id_token missing"):
            await handler.get_user_info(SSOTokenResponse("at123", None))

    async def test_get_user_info_email_verified_bool(self):
        handler = AppleSSOHandler()
        claims_data = {
            "sub": "apple_user_123",
            "email": "test@icloud.com",
            "email_verified": True,
        }
        id_token = jwt.encode(claims_data, "secret", algorithm="HS256")

        user_info = await handler.get_user_info(SSOTokenResponse("at123", id_token))
        assert user_info.email_verified is True

    async def test_get_user_info_no_email_verified(self):
        handler = AppleSSOHandler()
        claims_data = {
            "sub": "apple_user_123",
            "email": "test@icloud.com",
        }
        id_token = jwt.encode(claims_data, "secret", algorithm="HS256")

        user_info = await handler.get_user_info(SSOTokenResponse("at123", id_token))
        assert user_info.email_verified is False


class TestFacebookSSOHandler:
    def test_get_authorize_url(self):
        handler = FacebookSSOHandler()
        url = handler.get_authorize_url("state123", "http://localhost/callback")
        assert "facebook.com" in url
        assert "state=state123" in url
        assert "scope=email" in url
        assert "response_type=code" in url

    async def test_exchange_code_success(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"access_token": "fbtoken"}

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        result = await handler.exchange_code("code", "http://localhost/cb")
        assert result.access_token == "fbtoken"

    async def test_exchange_code_failure(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 400

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Facebook token exchange failed"):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_exchange_code_error_in_response(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"error": "oauth_error", "error_message": "invalid code"}

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Facebook token exchange error"):
            await handler.exchange_code("bad_code", "http://localhost/cb")

    async def test_get_user_info_success(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "fb123",
            "name": "FB User",
            "email": "test@fb.com",
            "picture": {"data": {"url": "http://pic.png"}},
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("fbtoken"))
        assert user_info.provider == "facebook"
        assert user_info.provider_id == "fb123"
        assert user_info.email == "test@fb.com"
        assert user_info.avatar_url == "http://pic.png"
        assert user_info.email_verified is True

    async def test_get_user_info_no_picture(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "fb123",
            "name": "FB User",
            "email": "test@fb.com",
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("fbtoken"))
        assert user_info.avatar_url is None

    async def test_get_user_info_failure(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        with pytest.raises(ValueError, match="Facebook userinfo request failed"):
            await handler.get_user_info(SSOTokenResponse("fbtoken"))

    async def test_get_user_info_picture_data_not_dict(self):
        handler = FacebookSSOHandler()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "id": "fb123",
            "name": "FB User",
            "email": "test@fb.com",
            "picture": "not_a_dict",
        }

        mock_client = _make_mock_client()
        mock_client.get.return_value = mock_response
        handler._get_http_client = AsyncMock(return_value=mock_client)

        user_info = await handler.get_user_info(SSOTokenResponse("fbtoken"))
        assert user_info.avatar_url is None


class TestSupportedProviders:
    def test_supported_providers_tuple(self):
        assert isinstance(SUPPORTED_PROVIDERS, tuple)
        assert len(SUPPORTED_PROVIDERS) == 5
        assert "google" in SUPPORTED_PROVIDERS
        assert "azure_ad" in SUPPORTED_PROVIDERS
        assert "github" in SUPPORTED_PROVIDERS
        assert "apple" in SUPPORTED_PROVIDERS
        assert "facebook" in SUPPORTED_PROVIDERS
