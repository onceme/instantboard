import ipaddress
from contextvars import ContextVar

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthRequired, Forbidden, InvalidToken
from app.core.redis import get_redis_client
from app.core.security import extract_user_from_token, is_token_blacklisted
from app.db.session import get_db_session

security = HTTPBearer(auto_error=False)

_raw_token_var: ContextVar[str | None] = ContextVar("_raw_token_var", default=None)


async def get_db(
    session: AsyncSession = Depends(get_db_session),
) -> AsyncSession:
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
