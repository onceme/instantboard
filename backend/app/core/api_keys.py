"""Centralized API key management with Redis-backed rotation and rate-limit tracking.

Each service (e.g. "finnhub", "alpha_vantage") owns a key pool. Redis state per service:

- ``apikey:rotation:{service}``                  rotation index, INCR on every get_key
- ``apikey:limited:{service}:{key_hash}``        429 rate-limit marker, expires after the cooldown
- ``apikey:invalid:{service}:{key_hash}``        401/403 invalid marker, no TTL (permanent until cleared)

Markers are keyed by the first 12 hex chars of md5(key) so raw keys never land in Redis.

Fail-open: every Redis operation swallows its own errors (logged) and ``get_key`` degrades
to in-process round-robin, so a Redis outage cannot halt collection.
"""

import hashlib
import logging

from app.core import redis as redis_core
from app.core.redis import RedisKeys

logger = logging.getLogger(__name__)

DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 60


class APIKeyPoolError(Exception):
    """Raised when the key pool cannot serve a key."""

    def __init__(self, service: str, message: str) -> None:
        self.service = service
        super().__init__(message)


class AllKeysRateLimited(APIKeyPoolError):
    """Every key in the pool is currently rate limited (transient; markers expire)."""

    def __init__(self, service: str) -> None:
        super().__init__(service, f"All API keys for service '{service}' are rate limited")


class AllKeysUnavailable(APIKeyPoolError):
    """The pool is empty or every key is marked invalid (permanent until keys change)."""

    def __init__(self, service: str) -> None:
        super().__init__(service, f"No usable API keys for service '{service}'")


def key_hash(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


class APIKeyManager:
    """Manages a pool of API keys for a single upstream service.

    Usage::

        manager = APIKeyManager("finnhub", ["k1", "k2"])
        key = await manager.get_key()          # raises AllKeysRateLimited / AllKeysUnavailable
        await manager.mark_rate_limited(key)   # after a 429
        await manager.mark_invalid(key)        # after a 401/403
        await manager.clear(key)               # manual recovery

    Pass ``redis_client`` to bypass the shared client (used by tests with MockRedis).
    """

    def __init__(self, service: str, keys: list[str], redis_client: object | None = None) -> None:
        self.service = service
        self.keys = list(keys)
        self._client = redis_client
        # In-process round-robin cursor used only when Redis is unavailable.
        self._fallback_index = 0

    async def _get_client(self):
        if self._client is not None:
            return self._client
        return await redis_core.get_redis_client()

    @property
    def rotation_key(self) -> str:
        return RedisKeys.APIKEY_ROTATION.format(service=self.service)

    def limited_key(self, key: str) -> str:
        return RedisKeys.APIKEY_LIMITED.format(service=self.service, key_hash=key_hash(key))

    def invalid_key(self, key: str) -> str:
        return RedisKeys.APIKEY_INVALID.format(service=self.service, key_hash=key_hash(key))

    async def get_key(self) -> str:
        """Return the next usable key: rotate via the Redis index, skipping keys marked
        invalid or rate limited, scanning at most one full lap of the pool.

        Raises AllKeysUnavailable when the pool is empty or every key is invalid,
        AllKeysRateLimited when every remaining key is rate limited. Falls back to
        in-process round-robin if Redis is unavailable.
        """
        if not self.keys:
            raise AllKeysUnavailable(self.service)
        try:
            client = await self._get_client()
            counter = int(await client.incr(self.rotation_key))
            start = (counter - 1) % len(self.keys)
            invalid_count = 0
            for offset in range(len(self.keys)):
                candidate = self.keys[(start + offset) % len(self.keys)]
                if await client.exists(self.invalid_key(candidate)):
                    invalid_count += 1
                    continue
                if await client.exists(self.limited_key(candidate)):
                    continue
                return candidate
            if invalid_count == len(self.keys):
                raise AllKeysUnavailable(self.service)
            raise AllKeysRateLimited(self.service)
        except APIKeyPoolError:
            raise
        except Exception as e:
            logger.warning(
                "APIKeyManager(%s): Redis unavailable, falling back to process-local round-robin: %s",
                self.service,
                e,
            )
            return self._next_fallback_key()

    async def mark_rate_limited(self, key: str, cooldown_seconds: int = DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS) -> None:
        """Mark a key as rate limited for ``cooldown_seconds`` (TTL-based, self-healing)."""
        try:
            client = await self._get_client()
            await client.set(self.limited_key(key), "1", ex=cooldown_seconds)
        except Exception as e:
            logger.warning("APIKeyManager(%s): failed to mark key rate limited: %s", self.service, e)

    async def mark_invalid(self, key: str) -> None:
        """Permanently flag a key invalid (401/403). No TTL: stays until clear() or key rotation."""
        try:
            client = await self._get_client()
            await client.set(self.invalid_key(key), "1")
        except Exception as e:
            logger.warning("APIKeyManager(%s): failed to mark key invalid: %s", self.service, e)

    async def clear(self, key: str) -> None:
        """Remove both rate-limit and invalid markers for a key (manual recovery)."""
        try:
            client = await self._get_client()
            await client.delete(self.limited_key(key))
            await client.delete(self.invalid_key(key))
        except Exception as e:
            logger.warning("APIKeyManager(%s): failed to clear key markers: %s", self.service, e)

    def _next_fallback_key(self) -> str:
        key = self.keys[self._fallback_index % len(self.keys)]
        self._fallback_index += 1
        return key
