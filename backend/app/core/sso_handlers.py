import logging
import time
from abc import ABC, abstractmethod

import httpx
from jose import jwt

from app.config import settings

logger = logging.getLogger(__name__)

SUPPORTED_PROVIDERS = ("google", "azure_ad", "github", "apple", "facebook")


class SSOUserInfo:
    provider: str
    provider_id: str
    email: str | None
    name: str | None
    avatar_url: str | None
    email_verified: bool
    raw_data: dict

    def __init__(
        self,
        provider: str,
        provider_id: str,
        email: str | None = None,
        name: str | None = None,
        avatar_url: str | None = None,
        email_verified: bool = False,
        raw_data: dict | None = None,
    ):
        self.provider = provider
        self.provider_id = provider_id
        self.email = email
        self.name = name
        self.avatar_url = avatar_url
        self.email_verified = email_verified
        self.raw_data = raw_data or {}


class SSOTokenResponse:
    access_token: str
    id_token: str | None

    def __init__(self, access_token: str, id_token: str | None = None):
        self.access_token = access_token
        self.id_token = id_token


class BaseSSOHandler(ABC):
    def __init__(self):
        self._http_client: httpx.AsyncClient | None = None

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=30.0)
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    @abstractmethod
    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        pass

    @abstractmethod
    async def exchange_code(self, code: str, redirect_uri: str) -> SSOTokenResponse:
        pass

    @abstractmethod
    async def get_user_info(self, token_response: SSOTokenResponse) -> SSOUserInfo:
        pass

    async def authenticate(self, code: str, redirect_uri: str) -> SSOUserInfo:
        token_response = await self.exchange_code(code, redirect_uri)
        user_info = await self.get_user_info(token_response)
        return user_info


class GoogleSSOHandler(BaseSSOHandler):
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": settings.google_oauth_client_id or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
        }
        return str(httpx.URL(self.AUTH_URL, params=params))

    async def exchange_code(self, code: str, redirect_uri: str) -> SSOTokenResponse:
        client = await self._get_http_client()
        data = {
            "client_id": settings.google_oauth_client_id,
            "client_secret": settings.google_oauth_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        response = await client.post(self.TOKEN_URL, data=data)
        if response.status_code != 200:
            logger.error("Google token exchange failed: status=%d", response.status_code)
            raise ValueError(f"Google token exchange failed: {response.status_code}")
        token_data = response.json()
        return SSOTokenResponse(
            access_token=token_data["access_token"],
            id_token=token_data.get("id_token"),
        )

    async def get_user_info(self, token_response: SSOTokenResponse) -> SSOUserInfo:
        client = await self._get_http_client()
        headers = {"Authorization": f"Bearer {token_response.access_token}"}
        response = await client.get(self.USERINFO_URL, headers=headers)
        if response.status_code != 200:
            logger.error("Google userinfo failed: status=%d", response.status_code)
            raise ValueError(f"Google userinfo request failed: {response.status_code}")
        data = response.json()
        return SSOUserInfo(
            provider="google",
            provider_id=data.get("sub", ""),
            email=data.get("email"),
            name=data.get("name"),
            avatar_url=data.get("picture"),
            email_verified=data.get("email_verified", False),
            raw_data=data,
        )


class AzureADSSOHandler(BaseSSOHandler):
    AUTH_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize"
    TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    USERINFO_URL = "https://graph.microsoft.com/v1.0/me"

    def _get_tenant_id(self) -> str:
        return settings.azure_ad_tenant_id

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        tenant_id = self._get_tenant_id()
        auth_url = self.AUTH_URL_TEMPLATE.format(tenant_id=tenant_id)
        params = {
            "client_id": settings.azure_ad_client_id or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile User.Read",
            "state": state,
            "response_mode": "query",
        }
        return str(httpx.URL(auth_url, params=params))

    async def exchange_code(self, code: str, redirect_uri: str) -> SSOTokenResponse:
        tenant_id = self._get_tenant_id()
        token_url = self.TOKEN_URL_TEMPLATE.format(tenant_id=tenant_id)
        client = await self._get_http_client()
        data = {
            "client_id": settings.azure_ad_client_id,
            "client_secret": settings.azure_ad_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": "openid email profile User.Read",
        }
        response = await client.post(token_url, data=data)
        if response.status_code != 200:
            logger.error("Azure AD token exchange failed: status=%d", response.status_code)
            raise ValueError(f"Azure AD token exchange failed: {response.status_code}")
        token_data = response.json()
        return SSOTokenResponse(
            access_token=token_data["access_token"],
            id_token=token_data.get("id_token"),
        )

    async def get_user_info(self, token_response: SSOTokenResponse) -> SSOUserInfo:
        client = await self._get_http_client()
        headers = {"Authorization": f"Bearer {token_response.access_token}"}
        response = await client.get(self.USERINFO_URL, headers=headers)
        if response.status_code != 200:
            logger.error("Azure AD userinfo failed: status=%d", response.status_code)
            raise ValueError(f"Azure AD userinfo request failed: {response.status_code}")
        data = response.json()
        return SSOUserInfo(
            provider="azure_ad",
            provider_id=data.get("id", ""),
            email=data.get("mail") or data.get("userPrincipalName"),
            name=data.get("displayName"),
            avatar_url=None,
            email_verified=True,
            raw_data=data,
        )


class GitHubSSOHandler(BaseSSOHandler):
    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"
    USERINFO_URL = "https://api.github.com/user"
    EMAIL_URL = "https://api.github.com/user/emails"

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": settings.github_oauth_client_id or "",
            "redirect_uri": redirect_uri,
            "scope": "user:email read:user",
            "state": state,
        }
        return str(httpx.URL(self.AUTH_URL, params=params))

    async def exchange_code(self, code: str, redirect_uri: str) -> SSOTokenResponse:
        client = await self._get_http_client()
        headers = {"Accept": "application/json"}
        data = {
            "client_id": settings.github_oauth_client_id,
            "client_secret": settings.github_oauth_client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
        response = await client.post(self.TOKEN_URL, data=data, headers=headers)
        if response.status_code != 200:
            logger.error("GitHub token exchange failed: status=%d", response.status_code)
            raise ValueError(f"GitHub token exchange failed: {response.status_code}")
        token_data = response.json()
        if "error" in token_data:
            logger.error("GitHub token exchange error: %s", token_data.get("error_description", token_data["error"]))
            raise ValueError(f"GitHub token exchange error: {token_data['error']}")
        return SSOTokenResponse(access_token=token_data["access_token"])

    async def get_user_info(self, token_response: SSOTokenResponse) -> SSOUserInfo:
        client = await self._get_http_client()
        headers = {"Authorization": f"token {token_response.access_token}"}

        response = await client.get(self.USERINFO_URL, headers=headers)
        if response.status_code != 200:
            logger.error("GitHub userinfo failed: status=%d", response.status_code)
            raise ValueError(f"GitHub userinfo request failed: {response.status_code}")
        data = response.json()

        email = data.get("email")
        email_verified = False
        if not email:
            email, email_verified = await self._fetch_primary_email(client, headers)

        return SSOUserInfo(
            provider="github",
            provider_id=str(data.get("id", "")),
            email=email,
            name=data.get("name") or data.get("login"),
            avatar_url=data.get("avatar_url"),
            email_verified=email_verified,
            raw_data=data,
        )

    async def _fetch_primary_email(self, client: httpx.AsyncClient, headers: dict) -> tuple[str | None, bool]:
        response = await client.get(self.EMAIL_URL, headers=headers)
        if response.status_code != 200:
            return None, False
        emails = response.json()
        for entry in emails:
            if entry.get("primary") and entry.get("verified"):
                return entry.get("email"), True
        for entry in emails:
            if entry.get("verified"):
                return entry.get("email"), True
        return None, False


class AppleSSOHandler(BaseSSOHandler):
    AUTH_URL = "https://appleid.apple.com/auth/authorize"
    TOKEN_URL = "https://appleid.apple.com/auth/token"

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": settings.apple_client_id or "",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "email name",
            "state": state,
            "response_mode": "form_post",
        }
        return str(httpx.URL(self.AUTH_URL, params=params))

    def _generate_client_secret(self) -> str:
        team_id = settings.apple_team_id
        client_id = settings.apple_client_id
        key_id = settings.apple_key_id

        if not all([team_id, client_id, key_id, settings.apple_private_key_path]):
            raise ValueError("Apple SSO configuration incomplete")

        with open(settings.apple_private_key_path) as f:
            private_key = f.read()

        now = int(time.time())
        claims = {
            "iss": team_id,
            "iat": now,
            "exp": now + 86400 * 180,
            "aud": "https://appleid.apple.com",
            "sub": client_id,
        }
        headers = {"kid": key_id, "alg": "ES256"}
        client_secret = jwt.encode(claims, private_key, algorithm="ES256", headers=headers)
        return client_secret

    async def exchange_code(self, code: str, redirect_uri: str) -> SSOTokenResponse:
        client = await self._get_http_client()
        client_secret = self._generate_client_secret()
        data = {
            "client_id": settings.apple_client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        response = await client.post(self.TOKEN_URL, data=data)
        if response.status_code != 200:
            logger.error("Apple token exchange failed: status=%d", response.status_code)
            raise ValueError(f"Apple token exchange failed: {response.status_code}")
        token_data = response.json()
        if "error" in token_data:
            logger.error("Apple token exchange error: %s", token_data.get("error_description", token_data["error"]))
            raise ValueError(f"Apple token exchange error: {token_data['error']}")
        return SSOTokenResponse(
            access_token=token_data.get("access_token", ""),
            id_token=token_data.get("id_token"),
        )

    async def get_user_info(self, token_response: SSOTokenResponse) -> SSOUserInfo:
        id_token = token_response.id_token
        if not id_token:
            raise ValueError("Apple id_token missing from response")

        claims = jwt.get_unverified_claims(id_token)
        provider_id = claims.get("sub", "")
        email = claims.get("email")
        email_verified = claims.get("email_verified", False)

        return SSOUserInfo(
            provider="apple",
            provider_id=provider_id,
            email=email,
            name=None,
            avatar_url=None,
            email_verified=bool(email_verified) if isinstance(email_verified, (str, bool)) else False,
            raw_data=claims,
        )


class FacebookSSOHandler(BaseSSOHandler):
    AUTH_URL = "https://www.facebook.com/v18.0/dialog/oauth"
    TOKEN_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
    USERINFO_URL = "https://graph.facebook.com/me"

    def get_authorize_url(self, state: str, redirect_uri: str) -> str:
        params = {
            "client_id": settings.facebook_app_id or "",
            "redirect_uri": redirect_uri,
            "scope": "email public_profile",
            "state": state,
            "response_type": "code",
        }
        return str(httpx.URL(self.AUTH_URL, params=params))

    async def exchange_code(self, code: str, redirect_uri: str) -> SSOTokenResponse:
        client = await self._get_http_client()
        params = {
            "client_id": settings.facebook_app_id,
            "client_secret": settings.facebook_app_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        response = await client.get(self.TOKEN_URL, params=params)
        if response.status_code != 200:
            logger.error("Facebook token exchange failed: status=%d", response.status_code)
            raise ValueError(f"Facebook token exchange failed: {response.status_code}")
        token_data = response.json()
        if "error" in token_data:
            logger.error("Facebook token exchange error: %s", token_data.get("error_message", token_data["error"]))
            raise ValueError(f"Facebook token exchange error: {token_data['error']}")
        return SSOTokenResponse(access_token=token_data["access_token"])

    async def get_user_info(self, token_response: SSOTokenResponse) -> SSOUserInfo:
        client = await self._get_http_client()
        params = {
            "fields": "id,name,email,picture.width(200).height(200)",
            "access_token": token_response.access_token,
        }
        response = await client.get(self.USERINFO_URL, params=params)
        if response.status_code != 200:
            logger.error("Facebook userinfo failed: status=%d", response.status_code)
            raise ValueError(f"Facebook userinfo request failed: {response.status_code}")
        data = response.json()

        avatar_url = None
        picture_data = data.get("picture", {})
        if isinstance(picture_data, dict):
            pic_data = picture_data.get("data", {})
            if isinstance(pic_data, dict):
                avatar_url = pic_data.get("url")

        return SSOUserInfo(
            provider="facebook",
            provider_id=data.get("id", ""),
            email=data.get("email"),
            name=data.get("name"),
            avatar_url=avatar_url,
            email_verified=bool(data.get("email")),
            raw_data=data,
        )


class SSOHandlerFactory:
    _handlers: dict[str, type[BaseSSOHandler]] = {
        "google": GoogleSSOHandler,
        "azure_ad": AzureADSSOHandler,
        "github": GitHubSSOHandler,
        "apple": AppleSSOHandler,
        "facebook": FacebookSSOHandler,
    }

    @classmethod
    def create(cls, provider: str) -> BaseSSOHandler:
        handler_class = cls._handlers.get(provider)
        if handler_class is None:
            raise ValueError(f"Unsupported SSO provider: {provider}")
        return handler_class()

    @classmethod
    def get_supported_providers(cls) -> list[str]:
        return list(cls._handlers.keys())
