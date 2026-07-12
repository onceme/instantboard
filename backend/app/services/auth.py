import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import (
    AuthRequired,
    InvalidOAuthCode,
    InvalidRefreshToken,
    InvalidToken,
    SSOProviderError,
    ValidationError,
)
from app.core.redis import RedisKeys, redis_delete, redis_set
from app.core.security import (
    add_token_to_blacklist,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_access_token_remaining_seconds,
    is_refresh_token_blacklisted,
)
from app.core.sso_handlers import SUPPORTED_PROVIDERS, SSOHandlerFactory
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def sso_login(self, provider: str, code: str, redirect_uri: str) -> dict:
        if provider not in SUPPORTED_PROVIDERS:
            raise ValidationError(
                message=f"Unsupported SSO provider: {provider}",
                details=[{"field": "provider", "message": f"Must be one of: {', '.join(SUPPORTED_PROVIDERS)}"}],
            )

        try:
            handler = SSOHandlerFactory.create(provider, settings)
        except ValueError as e:
            raise ValidationError(
                message=str(e),
                details=[{"field": "provider", "message": str(e)}],
            )
        try:
            user_info = await handler.authenticate(code, redirect_uri)
        except ValueError as e:
            logger.error("SSO authentication failed for provider=%s: %s", provider, str(e)[:200])
            raise SSOProviderError(message=f"{provider} authentication failed") from e
        except Exception as e:
            logger.error("SSO provider error for provider=%s: %s", provider, str(e)[:200])
            raise SSOProviderError(message=f"{provider} returned an error") from e
        finally:
            await handler.close()

        if not user_info.provider_id:
            raise InvalidOAuthCode(message="OAuth code exchange returned no user ID")

        user = await self._get_or_create_user(provider, user_info)

        token_data = {
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "provider": provider,
        }
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

        user.last_login_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(user)

        session_id = str(uuid.uuid4())
        session_key = RedisKeys.session_key(session_id)
        session_data = {
            "user_id": str(user.id),
            "tenant_id": str(user.tenant_id),
            "provider": provider,
        }
        await redis_set(session_key, session_data, ex=86400)

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": settings.jwt_access_token_expire_minutes * 60,
            "user": {
                "id": str(user.id),
                "email": user.email,
                "name": user.name,
                "avatar_url": user.avatar_url,
                "tenant_id": str(user.tenant_id),
                "role": user.role,
                "sso_provider": user.sso_provider,
            },
        }

    async def refresh_token(self, refresh_token_str: str) -> dict:
        payload = decode_token(refresh_token_str)
        if payload is None:
            raise InvalidRefreshToken()
        if payload.get("type") != "refresh":
            raise InvalidRefreshToken()

        refresh_version = payload.get("refresh_version")
        if refresh_version and await is_refresh_token_blacklisted(refresh_version):
            raise InvalidRefreshToken(message="Refresh token has been revoked")

        user_id = payload.get("sub")
        provider = payload.get("provider")

        result = await self.db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user is None:
            raise InvalidRefreshToken(message="User not found")

        await add_token_to_blacklist(refresh_token_str)

        token_data = {
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "provider": provider or user.sso_provider,
        }
        new_access_token = create_access_token(token_data)
        new_refresh_token = create_refresh_token(token_data)

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "expires_in": settings.jwt_access_token_expire_minutes * 60,
        }

    async def get_current_user(self, user_id: str, tenant_id: str) -> dict:
        result = await self.db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        if user is None:
            raise InvalidToken(message="User not found")
        if str(user.tenant_id) != tenant_id:
            raise AuthRequired(message="Tenant mismatch")

        return {
            "id": str(user.id),
            "email": user.email,
            "name": user.name,
            "avatar_url": user.avatar_url,
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "sso_provider": user.sso_provider,
        }

    async def logout(self, access_token: str, refresh_token_str: str | None = None, user_id: str | None = None) -> dict:
        remaining = get_access_token_remaining_seconds(access_token)
        if remaining > 0:
            await add_token_to_blacklist(access_token, ttl_seconds=remaining)

        if refresh_token_str:
            await add_token_to_blacklist(refresh_token_str, ttl_seconds=settings.jwt_refresh_token_expire_days * 86400)

        if user_id:
            pattern = "session:*"
            keys = []
            async for key in self.redis.scan_iter(match=pattern):
                val = await self.redis.get(key)
                if val:
                    try:
                        import json

                        data = json.loads(val)
                        if data.get("user_id") == user_id:
                            keys.append(key)
                    except (json.JSONDecodeError, TypeError):
                        pass
            for key in keys:
                await redis_delete(key)

        return {"message": "Logged out"}

    async def _get_or_create_user(self, provider: str, sso_user_info) -> User:
        result = await self.db.execute(
            select(User).where(
                User.sso_provider == provider,
                User.sso_provider_id == sso_user_info.provider_id,
            )
        )
        user = result.scalar_one_or_none()

        if user:
            if sso_user_info.email and user.email != sso_user_info.email:
                user.email = sso_user_info.email
            if sso_user_info.name and user.name != sso_user_info.name:
                user.name = sso_user_info.name
            if sso_user_info.avatar_url and user.avatar_url != sso_user_info.avatar_url:
                user.avatar_url = sso_user_info.avatar_url
            await self.db.flush()
            return user

        if sso_user_info.email:
            result = await self.db.execute(select(User).where(User.email == sso_user_info.email))
            existing_by_email = result.scalar_one_or_none()
            if existing_by_email:
                existing_by_email.sso_provider = provider
                existing_by_email.sso_provider_id = sso_user_info.provider_id
                if sso_user_info.name:
                    existing_by_email.name = sso_user_info.name
                if sso_user_info.avatar_url:
                    existing_by_email.avatar_url = sso_user_info.avatar_url
                await self.db.flush()
                return existing_by_email

        default_tenant = await self._get_default_tenant()

        email = sso_user_info.email or f"{provider}_{sso_user_info.provider_id}@sso.instantboard.dev"
        name = sso_user_info.name or f"{provider} User"

        new_user = User(
            tenant_id=default_tenant.id,
            email=email,
            name=name,
            avatar_url=sso_user_info.avatar_url,
            sso_provider=provider,
            sso_provider_id=sso_user_info.provider_id,
            role="member",
        )
        self.db.add(new_user)
        await self.db.flush()
        return new_user

    async def _get_default_tenant(self) -> Tenant:
        result = await self.db.execute(select(Tenant).where(Tenant.slug == settings.default_tenant_slug))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                name=settings.default_tenant_name,
                slug=settings.default_tenant_slug,
                plan="free",
                settings={},
            )
            self.db.add(tenant)
            await self.db.flush()
        return tenant
