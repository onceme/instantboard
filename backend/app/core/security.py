import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings
from app.core.redis import redis_get, redis_set

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(data: dict[str, Any], expires_minutes: int | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(UTC)
    expire = now + timedelta(minutes=expires_minutes or settings.jwt_access_token_expire_minutes)
    jti = str(uuid.uuid4())
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": jti,
            "type": "access",
        }
    )
    return jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(data: dict[str, Any], expires_days: int | None = None) -> str:
    to_encode = data.copy()
    now = datetime.now(UTC)
    expire = now + timedelta(days=expires_days or settings.jwt_refresh_token_expire_days)
    refresh_version = str(uuid.uuid4())
    to_encode.update(
        {
            "exp": expire,
            "iat": now,
            "jti": refresh_version,
            "type": "refresh",
            "refresh_version": refresh_version,
        }
    )
    token = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token


def decode_token(token: str) -> dict[str, Any] | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


async def add_token_to_blacklist(token: str, ttl_seconds: int | None = None) -> None:
    payload = decode_token(token)
    if payload is None:
        return
    jti = payload.get("jti")
    if not jti:
        return
    if ttl_seconds is None:
        exp = payload.get("exp", 0)
        now = int(datetime.now(UTC).timestamp())
        ttl_seconds = max(exp - now, 0)
    await redis_set(f"token_blacklist:{jti}", "1", ex=ttl_seconds)


async def is_token_blacklisted(token: str) -> bool:
    payload = decode_token(token)
    if payload is None:
        return False
    jti = payload.get("jti")
    if not jti:
        return False
    result = await redis_get(f"token_blacklist:{jti}")
    return result is not None


async def blacklist_refresh_token(refresh_token: str) -> None:
    await add_token_to_blacklist(refresh_token)


async def is_refresh_token_blacklisted(refresh_version: str) -> bool:
    result = await redis_get(f"token_blacklist:{refresh_version}")
    return result is not None


def extract_user_from_token(token: str) -> dict[str, Any] | None:
    payload = decode_token(token)
    if payload is None:
        return None
    if payload.get("type") != "access":
        return None
    return {
        "user_id": payload.get("sub"),
        "tenant_id": payload.get("tenant_id"),
        "role": payload.get("role"),
        "provider": payload.get("provider"),
    }


def get_access_token_remaining_seconds(token: str) -> int:
    payload = decode_token(token)
    if payload is None:
        return 0
    exp = payload.get("exp", 0)
    now = int(datetime.now(UTC).timestamp())
    return max(int(exp - now), 0)
