import ipaddress
import logging
import uuid as uuid_mod
from contextvars import ContextVar

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthRequired, Forbidden, InvalidToken
from app.core.redis import get_redis_client
from app.core.security import extract_tenant_from_token_unverified, extract_user_from_token, is_token_blacklisted
from app.db.rls import bind_tenant_context, is_postgres_session
from app.db.session import get_db_session

logger = logging.getLogger("instantboard")

security = HTTPBearer(auto_error=False)

_raw_token_var: ContextVar[str | None] = ContextVar("_raw_token_var", default=None)


def _extract_raw_token(request: Request) -> str | None:
    """Best-effort token extraction: Authorization bearer first, then the SSE
    ``token`` query parameter (EventSource cannot send headers)."""
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    token = request.query_params.get("token")
    return token or None


async def get_db(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> AsyncSession:
    """Request-scoped DB session with the RLS tenant context attached.

    On PostgreSQL the caller's tenant_id is decoded from the JWT (decode only —
    signature/expiry verification stays with get_current_user) and bound as the
    transaction-local ``app.current_tenant_id`` GUC; on SQLite or when no token
    is present this is a no-op (zero behavior change). NEVER sets
    ``app.is_service``: that bypass is reserved for server-side background
    sessions (app/db/session.py::apply_service_context). Any failure here is
    logged and swallowed — a broken context setup must not take the API down;
    the session simply runs without a tenant GUC and RLS hides tenant rows.
    """
    try:
        if is_postgres_session(session):
            raw_token = _extract_raw_token(request)
            if raw_token:
                tenant_id = extract_tenant_from_token_unverified(raw_token)
                # Reject malformed claims before they reach the policy's
                # ``::uuid`` cast (a forged tenant_id must degrade to "no
                # context", not to a 500 on every query).
                if tenant_id:
                    try:
                        uuid_mod.UUID(tenant_id)
                    except ValueError:
                        tenant_id = None
                if tenant_id:
                    bind_tenant_context(session, tenant_id)
    except Exception as exc:
        logger.warning(f"RLS tenant context setup failed: {exc}")
    try:
        yield session
    finally:
        await session.close()


async def get_redis(
    redis_client: Redis = Depends(get_redis_client),
) -> Redis:
    return redis_client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    redis: Redis = Depends(get_redis),
) -> dict:
    if credentials is None:
        raise AuthRequired()
    token = credentials.credentials
    _raw_token_var.set(token)

    if await is_token_blacklisted(token):
        raise InvalidToken(message="Token has been revoked")

    user_info = extract_user_from_token(token)
    if user_info is None:
        raise InvalidToken()

    user_id = user_info.get("user_id")
    if not user_id:
        raise InvalidToken(message="Token missing user_id")

    return user_info


async def get_current_tenant(
    user: dict = Depends(get_current_user),
) -> str:
    tenant_id = user.get("tenant_id")
    if tenant_id is None:
        raise AuthRequired(message="Token missing tenant_id")
    return tenant_id


async def require_admin(
    user: dict = Depends(get_current_user),
) -> dict:
    role = user.get("role")
    if role != "admin":
        raise Forbidden(message="Admin role required")
    return user


async def get_optional_token(
    token: str | None = Query(default=None, alias="token"),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    redis: Redis = Depends(get_redis),
) -> dict | None:
    raw_token = None
    if token:
        raw_token = token
    elif credentials:
        raw_token = credentials.credentials

    if raw_token:
        _raw_token_var.set(raw_token)
        if await is_token_blacklisted(raw_token):
            raise InvalidToken(message="Token has been revoked")
        user_info = extract_user_from_token(raw_token)
        if user_info is None:
            raise InvalidToken()
        return user_info
    return None


def get_raw_token() -> str | None:
    return _raw_token_var.get()


def get_client_ip(request: Request) -> str:
    """Extract the real client IP from X-Forwarded-For.

    Trust model: a single trusted proxy layer (in this deployment, the user firewall +
    nginx). nginx appends the direct peer via $proxy_add_x_forwarded_for for the /api/
    locations, so the genuine peer address ends up as the RIGHTMOST hop; every hop to
    its left originates upstream (ultimately from the client itself) and is untrusted.
    The previous implementation read the leftmost hop, which let a client forge the
    address by simply sending its own X-Forwarded-For. We therefore walk the header
    right-to-left and return the first well-formed IP, skipping blank or unparsable
    segments. For multi-tier proxy deployments this "rightmost wins" rule must be
    replaced by an explicit trusted-hop-count setting.
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        for hop in reversed(forwarded_for.split(",")):
            candidate = hop.strip()
            if not candidate:
                continue
            try:
                ipaddress.ip_address(candidate)
            except ValueError:
                continue
            return candidate
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
