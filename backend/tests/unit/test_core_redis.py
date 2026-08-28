import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.redis import (
    RedisKeys,
    close_redis,
    get_redis_client,
    get_redis_pool,
    redis_delete,
    redis_get,
    redis_hgetall,
    redis_hset,
    redis_publish,
    redis_sadd,
    redis_set,
    redis_sismember,
    redis_smembers,
    redis_srem,
)


class TestRedisKeys:
    def test_session_key(self):
        assert RedisKeys.session_key("abc123") == "session:abc123"

    def test_quote_key(self):
        assert RedisKeys.quote_key("tenant1", "AAPL") == "t:tenant1:quote:AAPL"

    def test_market_indices_key(self):
        assert RedisKeys.market_indices_key("tenant1") == "t:tenant1:market_indices"

    def test_commodities_key(self):
        assert RedisKeys.commodities_key("tenant1") == "t:tenant1:commodities"

    def test_nav_key(self):
        assert RedisKeys.nav_key("tenant1", "VFINX") == "t:tenant1:nav:VFINX"

    def test_channel_key(self):
        assert RedisKeys.channel_key("finance") == "channel:finance"

    def test_rate_limit_key(self):
        assert RedisKeys.rate_limit_key("t1", "1.2.3.4", "/api/test") == "rate:t1:1.2.3.4:/api/test"

    def test_dedup_key(self):
        assert RedisKeys.dedup_key("tenant1", "src1") == "t:tenant1:dedup:src1"

    def test_watchlist_key(self):
        assert RedisKeys.watchlist_key("tenant1", "user1") == "t:tenant1:watchlist:user1"

    def test_source_health_key(self):
        assert RedisKeys.source_health_key("src1") == "source_health:src1"

    def test_sso_state_key(self):
        assert RedisKeys.sso_state_key("state123") == "sso_state:state123"

    def test_search_key(self):
        assert RedisKeys.search_key("tenant1", "hash123") == "t:tenant1:search:hash123"

    def test_search_ttl(self):
        assert RedisKeys.SEARCH_TTL == 300

    def test_system_metrics_constant(self):
        assert RedisKeys.SYSTEM_METRICS == "dashboard:system_metrics"

    def test_ip_blacklist_constant(self):
        assert RedisKeys.IP_BLACKLIST == "ip_blacklist"


class TestGetRedisPool:
    async def test_creates_pool_once(self):
        with patch("app.core.redis._pool", None), patch("app.core.redis.ConnectionPool") as mock_pool_cls:
            mock_pool = MagicMock()
            mock_pool_cls.from_url.return_value = mock_pool

            pool1 = await get_redis_pool()
            pool2 = await get_redis_pool()

            mock_pool_cls.from_url.assert_called_once()
            assert pool1 is pool2

    async def test_returns_existing_pool(self):
        mock_pool = MagicMock()
        with patch("app.core.redis._pool", mock_pool):
            pool = await get_redis_pool()
            assert pool is mock_pool


class TestGetRedisClient:
    async def test_creates_client_once(self):
        mock_pool = MagicMock()
        with (
            patch("app.core.redis._pool", mock_pool),
            patch("app.core.redis._client", None),
            patch("app.core.redis.Redis") as mock_redis_cls,
        ):
            mock_client = MagicMock()
            mock_redis_cls.return_value = mock_client

            client1 = await get_redis_client()
            client2 = await get_redis_client()

            mock_redis_cls.assert_called_once()
            assert client1 is client2

    async def test_returns_existing_client(self):
        mock_client = MagicMock()
        with patch("app.core.redis._client", mock_client):
            client = await get_redis_client()
            assert client is mock_client


class TestCloseRedis:
    async def test_closes_client_and_pool(self):
        mock_client = AsyncMock()
        mock_pool = AsyncMock()
        with patch("app.core.redis._client", mock_client), patch("app.core.redis._pool", mock_pool):
            await close_redis()
            mock_client.aclose.assert_called_once()
            mock_pool.aclose.assert_called_once()

    async def test_handles_none_client_and_pool(self):
        with patch("app.core.redis._client", None), patch("app.core.redis._pool", None):
            await close_redis()


class TestRedisOperations:
    async def test_redis_get(self):
        mock_client = AsyncMock()
        mock_client.get.return_value = "value"
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_get("key1")
            assert result == "value"
            mock_client.get.assert_called_once_with("key1")

    async def test_redis_set_string(self):
        mock_client = AsyncMock()
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            await redis_set("key1", "value1", ex=60)
            mock_client.set.assert_called_once_with("key1", "value1", ex=60)

    async def test_redis_set_dict(self):
        mock_client = AsyncMock()
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            await redis_set("key1", {"a": 1})
            mock_client.set.assert_called_once_with("key1", '{"a": 1}', ex=None)

    async def test_redis_set_list(self):
        mock_client = AsyncMock()
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            await redis_set("key1", [1, 2, 3])
            mock_client.set.assert_called_once_with("key1", "[1, 2, 3]", ex=None)

    async def test_redis_delete(self):
        mock_client = AsyncMock()
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            await redis_delete("key1")
            mock_client.delete.assert_called_once_with("key1")

    async def test_redis_publish(self):
        mock_client = AsyncMock()
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            await redis_publish("channel", {"msg": "test"})
            mock_client.publish.assert_called_once_with("channel", '{"msg": "test"}')

    async def test_redis_hset(self):
        mock_client = AsyncMock()
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            await redis_hset("key1", {"field": "value"})
            mock_client.hset.assert_called_once_with("key1", mapping={"field": "value"})

    async def test_redis_hgetall(self):
        mock_client = AsyncMock()
        mock_client.hgetall.return_value = {"field": "value"}
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_hgetall("key1")
            assert result == {"field": "value"}
            mock_client.hgetall.assert_called_once_with("key1")

    async def test_redis_sadd(self):
        mock_client = AsyncMock()
        mock_client.sadd.return_value = 2
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_sadd("key1", "member1", "member2")
            assert result == 2
            mock_client.sadd.assert_called_once_with("key1", "member1", "member2")
            mock_client.expire.assert_not_called()

    async def test_redis_sadd_with_ttl_sets_expire(self):
        mock_client = AsyncMock()
        mock_client.sadd.return_value = 1
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_sadd("key1", "member1", ttl=86400)
            assert result == 1
            mock_client.sadd.assert_called_once_with("key1", "member1")
            mock_client.expire.assert_awaited_once_with("key1", 86400)

    async def test_redis_sismember(self):
        mock_client = AsyncMock()
        mock_client.sismember.return_value = True
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_sismember("key1", "member1")
            assert result is True
            mock_client.sismember.assert_called_once_with("key1", "member1")

    async def test_redis_smembers(self):
        mock_client = AsyncMock()
        mock_client.smembers.return_value = {"member1", "member2"}
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_smembers("key1")
            assert result == {"member1", "member2"}
            mock_client.smembers.assert_called_once_with("key1")

    async def test_redis_srem(self):
        mock_client = AsyncMock()
        mock_client.srem.return_value = 1
        with patch("app.core.redis.get_redis_client", return_value=mock_client):
            result = await redis_srem("key1", "member1", "member2")
            assert result == 1
            mock_client.srem.assert_called_once_with("key1", "member1", "member2")
