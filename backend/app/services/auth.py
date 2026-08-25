import contextlib
import logging
import uuid
from datetime import UTC, datetime

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import (
    ADMIN_LOGIN_IP_LOCK_SECONDS,
    ADMIN_LOGIN_IP_MAX_FAILURES,
    ADMIN_LOGIN_IP_WINDOW_SECONDS,
    ADMIN_LOGIN_LOCK_SECONDS,
    ADMIN_LOGIN_MAX_FAILURES,
    ADMIN_LOGIN_WINDOW_SECONDS,
    LOCAL_SSO_PROVIDER,
    SYSTEM_TENANT_ID,
)
from app.core.exceptions import (
    AdminLoginDisabled,
    AuthRequired,
    InvalidCredentials,
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
    verify_password,
)
from app.core.sso_handlers import SUPPORTED_PROVIDERS, SSOHandlerFactory
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

# Pre-computed bcrypt hash of a random throwaway password. It is verified against on
# unknown-email attempts so both failure paths take comparable time (timing equalization).
_DUMMY_BCRYPT_HASH = "$2b$12$t6Ja/Sd8rBqSkxTOLwQp..D5.dnLcwwSqHe3yfVcNTmrNReirB7ua"


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
            ) from e
        try:
            user_info = await handler.authenticate(code, redirect_uri)
        except ValueError as e:
            logger.error("SSO authentication failed for provider=%s: %s", provider, str(e)[:200])
            raise SSOProviderError(message=f"{provider} authentication failed") from e
        except Exception as e:
            # Fix: log the full stack trace for upstream failures to ease debugging, and put
            # the exception class name + message into the error response's details field
            # (HTTP 502). The message is truncated to avoid excessive length.
            logger.exception("SSO provider error for provider=%s", provider)
            raise SSOProviderError(
                message=f"{provider} returned an error",
                details=[{"exception": type(e).__name__, "message": str(e)[:500]}],
            ) from e
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

    async def admin_login(self, email: str, password: str, client_ip: str) -> dict:
        """Local admin login (isolated identity model, see docs/dev-guide/design/admin-login.md).

        The admin user record lives in the system tenant with provider='local' and is
        never merged with SSO users, even when the email matches.
        """
        email = email.strip().lower()

        if not settings.admin_login_enabled:
            raise AdminLoginDisabled()

        await self._check_admin_login_locks(email, client_ip)

        result = await self.db.execute(
            select(User).where(
                User.sso_provider == LOCAL_SSO_PROVIDER,
                User.sso_provider_id == self._local_provider_id(email),
            )
        )
        user = result.scalar_one_or_none()

        configured_email = (settings.admin_email or "").strip().lower()
        if email == configured_email:
            try:
                password_ok = verify_password(password, settings.admin_password_hash or "")
            except ValueError:
                logger.error("ADMIN_PASSWORD_HASH is not a valid bcrypt hash")
                password_ok = False
        else:
            # Unknown email: run a dummy verification to equalize response timing.
            with contextlib.suppress(ValueError):
                verify_password(password, _DUMMY_BCRYPT_HASH)
            password_ok = False

        if not password_ok:
            # Never reveal which reason applied; log email + IP only, never the password.
            logger.warning("Admin login failed: email=%s ip=%s", email, client_ip)
            await self._record_admin_login_failure(email, client_ip)
            raise InvalidCredentials()

        await self._clear_admin_login_failures(email)

        user = await self._upsert_admin_user(email, user)
        user.last_login_at = datetime.now(UTC)
        await self.db.commit()
        await self.db.refresh(user)

        token_data = {
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role,
            "provider": LOCAL_SSO_PROVIDER,
        }
        access_token = create_access_token(token_data)
        refresh_token = create_refresh_token(token_data)

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

        # Kill-switch for local admin sessions: once ADMIN_EMAIL/ADMIN_PASSWORD_HASH are
        # removed from the environment (admin_login_enabled False), existing local
        # sessions can no longer renew their tokens. Rejected before rotation/blacklist
        # so the same refresh token works again if the configuration is restored.
        provider = payload.get("provider")
        if provider == LOCAL_SSO_PROVIDER and not settings.admin_login_enabled:
            logger.warning("Rejected refresh for local admin session: admin login is disabled")
            raise InvalidRefreshToken(message="Admin login is disabled")

        refresh_version = payload.get("refresh_version")
        if refresh_version and await is_refresh_token_blacklisted(refresh_version):
            raise InvalidRefreshToken(message="Refresh token has been revoked")

        user_id = payload.get("sub")

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
        # Main path: an existing user with the same (provider, provider_id) just gets
        # their profile refreshed and is returned unchanged otherwise.
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

        # New SSO identities are provisioned in the default tenant.
        default_tenant = await self._get_default_tenant()

        if sso_user_info.email:
            # Email fallback (original intent: profile backfill / binding an SSO identity
            # to an already-provisioned record for the same person instead of creating a
            # duplicate).
            #
            # Security fix (account takeover): this branch used to match ANY user row with
            # the same email, so a single SSO login could take over the local admin record
            # (sso_provider='local', system tenant, role='admin') and inherit its admin
            # role, or claim accounts belonging to other tenants. Post-fix semantics:
            #   * local accounts are excluded — the local admin record (and any other
            #     provider='local' identity) can never be claimed or rewritten by SSO;
            #     identities are isolated by login entry and never merged by email
            #     (docs/dev-guide/design/admin-login.md), so an SSO login with the admin email
            #     simply provisions a fresh member user;
            #   * the match is scoped to the default tenant — the only tenant a new SSO
            #     user belongs to — never crossing tenant boundaries.
            result = await self.db.execute(
                select(User).where(
                    User.email == sso_user_info.email,
                    User.tenant_id == default_tenant.id,
                    User.sso_provider != LOCAL_SSO_PROVIDER,
                )
            )
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

    @staticmethod
    def _local_provider_id(email: str) -> str:
        return f"{LOCAL_SSO_PROVIDER}:{email}"

    async def _check_admin_login_locks(self, email: str, client_ip: str) -> None:
        """Reject immediately when either dimension is locked. Redis failures fail open."""
        try:
            email_locked = await self.redis.get(RedisKeys.admin_login_lock_key(email)) is not None
            ip_locked = await self.redis.get(RedisKeys.admin_login_lock_ip_key(client_ip)) is not None
        except Exception as e:
            logger.warning("Redis unavailable during admin login lock check; failing open: %s", e)
            return
        if email_locked or ip_locked:
            raise InvalidCredentials()

    async def _record_admin_login_failure(self, email: str, client_ip: str) -> None:
        """Fixed-window INCR+EXPIRE counters for email and IP dimensions. Redis failures
        fail open (the login correctness decision never depends on Redis)."""
        try:
            await self._register_admin_login_failure(
                counter_key=RedisKeys.admin_login_fail_key(email),
                threshold=ADMIN_LOGIN_MAX_FAILURES,
                lock_key=RedisKeys.admin_login_lock_key(email),
                window_seconds=ADMIN_LOGIN_WINDOW_SECONDS,
                lock_seconds=ADMIN_LOGIN_LOCK_SECONDS,
            )
            await self._register_admin_login_failure(
                counter_key=RedisKeys.admin_login_fail_ip_key(client_ip),
                threshold=ADMIN_LOGIN_IP_MAX_FAILURES,
                lock_key=RedisKeys.admin_login_lock_ip_key(client_ip),
                window_seconds=ADMIN_LOGIN_IP_WINDOW_SECONDS,
                lock_seconds=ADMIN_LOGIN_IP_LOCK_SECONDS,
            )
        except Exception as e:
            logger.warning("Redis unavailable while recording admin login failure; failing open: %s", e)

    async def _register_admin_login_failure(
        self,
        counter_key: str,
        threshold: int,
        lock_key: str,
        window_seconds: int,
        lock_seconds: int,
    ) -> None:
        count = await self.redis.incr(counter_key)
        if count == 1:
            await self.redis.expire(counter_key, window_seconds)
        if count >= threshold:
            await self.redis.set(lock_key, "1", ex=lock_seconds)

    async def _clear_admin_login_failures(self, email: str) -> None:
        try:
            await self.redis.delete(RedisKeys.admin_login_fail_key(email))
        except Exception as e:
            logger.warning("Redis unavailable while clearing admin login failures: %s", e)

    async def _upsert_admin_user(self, email: str, user: User | None) -> User:
        """Lazy upsert keyed on (sso_provider='local', sso_provider_id='local:{email}').

        Existing records only get their profile refreshed (role is always 'admin'); new
        records are created in the system tenant, keeping them isolated from SSO users.
        """
        if user is not None:
            user.email = email
            user.role = "admin"
            await self.db.flush()
            return user

        system_tenant = await self._get_system_tenant()
        name = email.split("@", 1)[0] or "Admin"
        new_user = User(
            tenant_id=system_tenant.id,
            email=email,
            name=name[:100],
            sso_provider=LOCAL_SSO_PROVIDER,
            sso_provider_id=self._local_provider_id(email),
            role="admin",
        )
        self.db.add(new_user)
        await self.db.flush()
        return new_user

    async def _get_system_tenant(self) -> Tenant:
        result = await self.db.execute(select(Tenant).where(Tenant.slug == "system"))
        tenant = result.scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(
                id=SYSTEM_TENANT_ID,
                name="System",
                slug="system",
                plan="enterprise",
                settings={},
                max_users=100,
                max_categories=50,
                max_sources=200,
                is_active=True,
            )
            self.db.add(tenant)
            await self.db.flush()
        return tenant
