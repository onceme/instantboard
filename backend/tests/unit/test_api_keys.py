"""Unit tests for app/core/api_keys.py — centralized APIKeyManager."""

import hashlib

import pytest

from app.core.api_keys import (
    AllKeysRateLimited,
    AllKeysUnavailable,
    APIKeyManager,
    key_hash,
)
from app.core.redis import RedisKeys


def limited_key(service: str, key: str) -> str:
    return RedisKeys.APIKEY_LIMITED.format(service=service, key_hash=key_hash(key))


def invalid_key(service: str, key: str) -> str:
    return RedisKeys.APIKEY_INVALID.format(service=service, key_hash=key_hash(key))


class BrokenRedis:
    """Redis client where every command fails (simulates an unreachable server)."""

    async def incr(self, key):
        raise ConnectionError("redis down")

    async def exists(self, key):
        raise ConnectionError("redis down")

    async def set(self, key, value, ex=None):
        raise ConnectionError("redis down")

    async def delete(self, key):
        raise ConnectionError("redis down")


# ── rotation ──────────────────────────────────────────────────────
class TestRotation:
    async def test_rotation_advances_across_calls(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1", "k2", "k3"], redis_client=redis_mock)
        assert await manager.get_key() == "k1"
        assert await manager.get_key() == "k2"
        assert await manager.get_key() == "k3"
        assert await manager.get_key() == "k1"

    async def test_rotation_index_persisted_in_redis(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1", "k2"], redis_client=redis_mock)
        for _ in range(3):
            await manager.get_key()
        assert redis_mock._data[RedisKeys.APIKEY_ROTATION.format(service="finnhub")] == 3

    async def test_get_key_uses_rotation_index_from_redis(self, redis_mock):
        # A pre-existing index (e.g. written by another worker) is honored, so
        # rotation is shared across processes instead of restarting at key 0.
        await redis_mock.set(RedisKeys.APIKEY_ROTATION.format(service="finnhub"), "7")
        manager = APIKeyManager("finnhub", ["k1", "k2", "k3"], redis_client=redis_mock)
        # INCR -> 8, start index (8-1) % 3 == 1
        assert await manager.get_key() == "k2"


# ── rate-limit markers ────────────────────────────────────────────
class TestRateLimitMarkers:
    async def test_get_key_skips_rate_limited_key(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1", "k2", "k3"], redis_client=redis_mock)
        await manager.mark_rate_limited("k1")
        assert await manager.get_key() == "k2"

    async def test_rate_limited_key_recovers_after_cooldown(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1", "k2"], redis_client=redis_mock)
        await manager.mark_rate_limited("k1", cooldown_seconds=60)
        assert await manager.get_key() == "k2"
        # Marker still present before the cooldown elapses...
        assert await redis_mock.exists(limited_key("finnhub", "k1")) == 1
        redis_mock.advance(61)
        # ...and purged afterwards, so the key is served again within one lap.
        assert await redis_mock.exists(limited_key("finnhub", "k1")) == 0
        served = {await manager.get_key() for _ in range(2)}
        assert served == {"k1", "k2"}

    async def test_rate_limit_marker_written_with_cooldown_ttl(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1"], redis_client=redis_mock)
        await manager.mark_rate_limited("k1", cooldown_seconds=45)
        assert redis_mock._data[limited_key("finnhub", "k1")] == "1"
        assert redis_mock._expiry[limited_key("finnhub", "k1")] == 45

    async def test_default_cooldown_is_60_seconds(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1"], redis_client=redis_mock)
        await manager.mark_rate_limited("k1")
        assert redis_mock._expiry[limited_key("finnhub", "k1")] == 60


# ── invalid markers ───────────────────────────────────────────────
class TestInvalidMarkers:
    async def test_invalid_key_skipped_permanently(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1", "k2"], redis_client=redis_mock)
        await manager.mark_invalid("k1")
        for _ in range(4):
            assert await manager.get_key() == "k2"
        # Invalid markers carry no TTL: advancing the clock never revives the key.
        redis_mock.advance(10**6)
        for _ in range(2):
            assert await manager.get_key() == "k2"

    async def test_invalid_marker_written_without_ttl(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1"], redis_client=redis_mock)
        await manager.mark_invalid("k1")
        assert redis_mock._data[invalid_key("finnhub", "k1")] == "1"
        assert invalid_key("finnhub", "k1") not in redis_mock._expiry


# ── pool exhaustion ───────────────────────────────────────────────
class TestPoolExhaustion:
    async def test_all_keys_rate_limited_raises(self, redis_mock):
        manager = APIKeyManager("alpha_vantage", ["k1", "k2"], redis_client=redis_mock)
        await manager.mark_rate_limited("k1")
        await manager.mark_rate_limited("k2")
        with pytest.raises(AllKeysRateLimited) as exc_info:
            await manager.get_key()
        assert exc_info.value.service == "alpha_vantage"
        assert "alpha_vantage" in str(exc_info.value)

    async def test_all_keys_invalid_raises_unavailable(self, redis_mock):
        manager = APIKeyManager("alpha_vantage", ["k1", "k2"], redis_client=redis_mock)
        await manager.mark_invalid("k1")
        await manager.mark_invalid("k2")
        with pytest.raises(AllKeysUnavailable) as exc_info:
            await manager.get_key()
        assert exc_info.value.service == "alpha_vantage"

    async def test_mixed_invalid_and_limited_reports_transient_state(self, redis_mock):
        # Not every key is invalid, so the pool is expected to recover once the
        # rate-limit cooldowns expire -> AllKeysRateLimited, not Unavailable.
        manager = APIKeyManager("finnhub", ["k1", "k2"], redis_client=redis_mock)
        await manager.mark_invalid("k1")
        await manager.mark_rate_limited("k2")
        with pytest.raises(AllKeysRateLimited):
            await manager.get_key()

    async def test_empty_pool_raises_unavailable(self, redis_mock):
        manager = APIKeyManager("finnhub", [], redis_client=redis_mock)
        with pytest.raises(AllKeysUnavailable):
            await manager.get_key()


# ── Redis failure -> fail-open ────────────────────────────────────
class TestRedisFailOpen:
    async def test_get_key_falls_back_to_process_round_robin(self):
        manager = APIKeyManager("finnhub", ["k1", "k2"], redis_client=BrokenRedis())
        assert await manager.get_key() == "k1"
        assert await manager.get_key() == "k2"
        assert await manager.get_key() == "k1"

    async def test_mark_and_clear_swallow_redis_errors(self):
        manager = APIKeyManager("finnhub", ["k1"], redis_client=BrokenRedis())
        await manager.mark_rate_limited("k1")
        await manager.mark_invalid("k1")
        await manager.clear("k1")

    async def test_empty_pool_raises_even_when_redis_is_down(self):
        manager = APIKeyManager("finnhub", [], redis_client=BrokenRedis())
        with pytest.raises(AllKeysUnavailable):
            await manager.get_key()

    async def test_get_key_falls_back_when_client_resolution_fails(self, monkeypatch):
        async def broken_get_redis_client():
            raise ConnectionError("no redis server")

        monkeypatch.setattr("app.core.api_keys.redis_core.get_redis_client", broken_get_redis_client)
        manager = APIKeyManager("finnhub", ["k1", "k2"])
        assert await manager.get_key() == "k1"
        assert await manager.get_key() == "k2"


# ── markers & hashing ─────────────────────────────────────────────
class TestMarkersAndHashing:
    async def test_raw_key_never_appears_in_redis_key_names(self, redis_mock):
        manager = APIKeyManager("finnhub", ["super-secret-key"], redis_client=redis_mock)
        await manager.mark_rate_limited("super-secret-key")
        await manager.mark_invalid("super-secret-key")
        assert all("super-secret-key" not in k for k in redis_mock._data)

    async def test_key_hash_is_md5_first_12_hex(self):
        assert key_hash("abc") == hashlib.md5(b"abc").hexdigest()[:12]
        assert len(key_hash("abc")) == 12

    async def test_clear_removes_both_markers(self, redis_mock):
        manager = APIKeyManager("finnhub", ["k1"], redis_client=redis_mock)
        await manager.mark_rate_limited("k1")
        await manager.mark_invalid("k1")
        with pytest.raises(AllKeysUnavailable):
            await manager.get_key()
        await manager.clear("k1")
        assert await redis_mock.exists(limited_key("finnhub", "k1")) == 0
        assert await redis_mock.exists(invalid_key("finnhub", "k1")) == 0
        assert await manager.get_key() == "k1"
