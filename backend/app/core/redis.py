import json
from typing import Any

from redis.asyncio import ConnectionPool, Redis

from app.config import settings

_pool: ConnectionPool | None = None
_client: Redis | None = None


async def get_redis_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return _pool


async def get_redis_client() -> Redis:
    global _client
    if _client is None:
        pool = await get_redis_pool()
        _client = Redis(connection_pool=pool)
    return _client


async def close_redis():
    global _client, _pool
    if _client is not None:
        await _client.aclose()
        _client = None
    if _pool is not None:
        await _pool.aclose()
        _pool = None


class RedisKeys:
    SESSION = "session:{session_id}"
    QUOTE = "t:{tenant_id}:quote:{symbol}"
    MARKET_INDICES = "t:{tenant_id}:market_indices"
    COMMODITIES = "t:{tenant_id}:commodities"
    NAV = "t:{tenant_id}:nav:{symbol}"
    CHANNEL = "channel:{category}"
    RATE_LIMIT = "rate:{tenant_id}:{ip}:{endpoint}"
    DEDUP = "t:{tenant_id}:dedup:{source_id}"
    WATCHLIST = "t:{tenant_id}:watchlist:{user_id}"
    SOURCE_HEALTH = "source_health:{source_id}"
    SYSTEM_METRICS = "dashboard:system_metrics"
    SSO_STATE = "sso_state:{state_key}"
    IP_BLACKLIST = "ip_blacklist"
    SEARCH = "t:{tenant_id}:search:{query_hash}"
    ADMIN_LOGIN_FAIL = "admin_login:fail:{email}"
    ADMIN_LOGIN_LOCK = "admin_login:lock:{email}"
    ADMIN_LOGIN_FAIL_IP = "admin_login:fail_ip:{ip}"
    ADMIN_LOGIN_LOCK_IP = "admin_login:lock_ip:{ip}"
    WORKER_HEARTBEAT = "scheduler:worker:heartbeat"

    SEARCH_TTL = 300
    # Worker heartbeat TTL: 3x the 15s write interval (app/scheduler/worker.py).
    # Also used by the api side as the freshness threshold when judging worker
    # health from the heartbeat (app/services/dashboard.py).
    WORKER_HEARTBEAT_TTL = 45

    @staticmethod
    def session_key(session_id: str) -> str:
        return RedisKeys.SESSION.format(session_id=session_id)

    @staticmethod
    def quote_key(tenant_id: str, symbol: str) -> str:
        return RedisKeys.QUOTE.format(tenant_id=tenant_id, symbol=symbol)

    @staticmethod
    def market_indices_key(tenant_id: str) -> str:
        return RedisKeys.MARKET_INDICES.format(tenant_id=tenant_id)

    @staticmethod
    def commodities_key(tenant_id: str) -> str:
        return RedisKeys.COMMODITIES.format(tenant_id=tenant_id)

    @staticmethod
    def nav_key(tenant_id: str, symbol: str) -> str:
        return RedisKeys.NAV.format(tenant_id=tenant_id, symbol=symbol)

    @staticmethod
    def channel_key(category: str) -> str:
        return RedisKeys.CHANNEL.format(category=category)

    @staticmethod
    def rate_limit_key(tenant_id: str, ip: str, endpoint: str) -> str:
        return RedisKeys.RATE_LIMIT.format(tenant_id=tenant_id, ip=ip, endpoint=endpoint)

    @staticmethod
    def dedup_key(tenant_id: str, source_id: str) -> str:
        return RedisKeys.DEDUP.format(tenant_id=tenant_id, source_id=source_id)

    @staticmethod
    def watchlist_key(tenant_id: str, user_id: str) -> str:
        return RedisKeys.WATCHLIST.format(tenant_id=tenant_id, user_id=user_id)

    @staticmethod
    def source_health_key(source_id: str) -> str:
        return RedisKeys.SOURCE_HEALTH.format(source_id=source_id)

    @staticmethod
    def sso_state_key(state_key: str) -> str:
        return RedisKeys.SSO_STATE.format(state_key=state_key)

    @staticmethod
    def search_key(tenant_id: str, query_hash: str) -> str:
        return RedisKeys.SEARCH.format(tenant_id=tenant_id, query_hash=query_hash)

    @staticmethod
    def admin_login_fail_key(email: str) -> str:
        return RedisKeys.ADMIN_LOGIN_FAIL.format(email=email)

    @staticmethod
    def admin_login_lock_key(email: str) -> str:
        return RedisKeys.ADMIN_LOGIN_LOCK.format(email=email)

    @staticmethod
    def admin_login_fail_ip_key(ip: str) -> str:
        return RedisKeys.ADMIN_LOGIN_FAIL_IP.format(ip=ip)

    @staticmethod
    def admin_login_lock_ip_key(ip: str) -> str:
        return RedisKeys.ADMIN_LOGIN_LOCK_IP.format(ip=ip)

    @staticmethod
    def worker_heartbeat_key() -> str:
        return RedisKeys.WORKER_HEARTBEAT


async def redis_get(key: str) -> str | None:
    client = await get_redis_client()
    return await client.get(key)


async def redis_set(key: str, value: Any, ex: int | None = None) -> None:
    client = await get_redis_client()
    if isinstance(value, (dict, list)):
        value = json.dumps(value)
    await client.set(key, value, ex=ex)


async def redis_delete(key: str) -> None:
    client = await get_redis_client()
    await client.delete(key)


async def redis_publish(channel: str, message: dict) -> None:
    client = await get_redis_client()
    await client.publish(channel, json.dumps(message))


async def redis_hset(key: str, mapping: dict) -> None:
    client = await get_redis_client()
    await client.hset(key, mapping=mapping)


async def redis_hgetall(key: str) -> dict:
    client = await get_redis_client()
    return await client.hgetall(key)


async def redis_sadd(key: str, *members: str) -> int:
    client = await get_redis_client()
    return await client.sadd(key, *members)


async def redis_sismember(key: str, member: str) -> bool:
    client = await get_redis_client()
    return await client.sismember(key, member)
