import json
import logging
from typing import Any

from redis.asyncio import ConnectionPool, Redis

from app.config import settings

logger = logging.getLogger(__name__)

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
    # Read-through cache of FinanceService.get_watchlist's response list: written
    # on cache miss with REDIS_TTL_WATCHLIST (app/services/finance.py) and deleted
    # by every watchlist mutation (add/remove/reorder/alert-threshold PATCH). A
    # corrupt entry is dropped and rebuilt from PG; Redis down degrades the read
    # to a plain PG query (database.md §3.2).
    WATCHLIST = "t:{tenant_id}:watchlist:{user_id}"
    SOURCE_HEALTH = "source_health:{source_id}"
    SYSTEM_METRICS = "dashboard:system_metrics"
    # API request stats written by RequestLoggingMiddleware (core/middleware.py)
    # and read by DashboardService.get_api_request_stats() for the `api` group of
    # GET /dashboard/system. Totals accumulate across api restarts (no TTL); the
    # per-minute buckets expire on their own (see API_METRICS_MINUTE_TTL).
    API_METRICS_TOTALS = "dashboard:api_metrics:totals"
    API_METRICS_MINUTE = "dashboard:api_metrics:minute:{minute}"
    SSO_STATE = "sso_state:{state_key}"
    IP_BLACKLIST = "ip_blacklist"
    SEARCH = "t:{tenant_id}:search:{query_hash}"
    ADMIN_LOGIN_FAIL = "admin_login:fail:{email}"
    ADMIN_LOGIN_LOCK = "admin_login:lock:{email}"
    ADMIN_LOGIN_FAIL_IP = "admin_login:fail_ip:{ip}"
    ADMIN_LOGIN_LOCK_IP = "admin_login:lock_ip:{ip}"
    WORKER_HEARTBEAT = "scheduler:worker:heartbeat"
    # Cross-process SSE load gauge: the api process writes its in-router active
    # connection count here (core/sse_router.py register/unregister/heartbeat),
    # the worker's scheduler reads it back as one input of the load-aware
    # collection throttling multiplier (scheduler/manager.py
    # evaluate_load_multiplier, finance-tab.md §3.8.3). Plain integer string.
    SSE_ACTIVE_CONNECTIONS = "sse:active_connections"
    STREAM_HISTORY = "stream:history:{category}"
    APIKEY_ROTATION = "apikey:rotation:{service}"
    APIKEY_LIMITED = "apikey:limited:{service}:{key_hash}"
    APIKEY_INVALID = "apikey:invalid:{service}:{key_hash}"
    # Business metrics payload cached by DashboardService.get_business_metrics()
    # and served by GET /dashboard/business-metrics (dashboard-tab.md §3.5). The
    # aggregate JOIN/COUNT queries are expensive, so the whole response is cached
    # for BUSINESS_METRICS_TTL seconds.
    BUSINESS_METRICS = "dashboard:business_metrics"
    # Per-minute buckets of SSE push events written by SSEEventRouter.push_event
    # (core/sse_router.py) and summed over a sliding 60-minute window by
    # DashboardService._events_pushed_window().
    EVENTS_PUSHED_MINUTE = "dashboard:events_pushed:minute:{minute}"
    # Throttle marker written by SSEService.publish_topic_stats_update (SET NX EX)
    # so a tech source that stores items pushes at most one topic_stats_update per
    # tenant per TOPIC_STATS_PUSHED_TTL window. Deleted early when the stats load
    # fails so the next collection can retry within the window.
    TOPIC_STATS_PUSHED = "tech:topic_stats_pushed:{tenant_id}"
    # Cooldown marker written by FinanceService._check_alert_threshold (SET NX EX):
    # once a watchlist entry trips its alert threshold, at most one alert_update is
    # published per ALERT_FIRED_TTL window so a persistently-breached threshold does
    # not spam the user on every quote fetch (finance-tab.md §3.2). Redis
    # unavailable → detection is skipped (no alert), never an error.
    ALERT_FIRED = "finance:alert_fired:{tenant_id}:{item_id}"

    SEARCH_TTL = 300
    STREAM_HISTORY_LIMIT = 500
    STREAM_HISTORY_TTL = 1800
    # Worker heartbeat TTL: 3x the 15s write interval (app/scheduler/worker.py).
    # Also used by the api side as the freshness threshold when judging worker
    # health from the heartbeat (app/services/dashboard.py).
    WORKER_HEARTBEAT_TTL = 45
    # SSE active-connections gauge TTL: 2x the 30s heartbeat refresh interval so a
    # live api process never lets the key lapse, while a dead api process makes
    # the gauge disappear within one minute (the scheduler then degrades to the
    # normal collection frequency instead of throttling on stale numbers).
    SSE_ACTIVE_CONNECTIONS_TTL = 60
    # Minute-bucket TTL: covers the bucket being written plus the previous one,
    # which is all the sliding 60s window reader ever needs.
    API_METRICS_MINUTE_TTL = 120
    # Business metrics cache TTL (seconds) for BUSINESS_METRICS.
    BUSINESS_METRICS_TTL = 60
    # SSE push-event minute-bucket TTL. Unlike API_METRICS_MINUTE_TTL this must
    # keep every bucket alive for the FULL 60-minute read window, so the oldest
    # bucket the window ever reads (now - 59min) still exists when summed.
    EVENTS_PUSHED_MINUTE_TTL = 62 * 60
    # topic_stats_update throttle window (seconds). Matches the 900s REST topics
    # cache in TechService.get_topics so the pushed payload is never fresher than
    # what GET /tech/topics serves (tech-tab.md §3.8).
    TOPIC_STATS_PUSHED_TTL = 900
    # Watchlist price-alert cooldown window (seconds): one alert_update per item
    # per hour while the threshold stays breached (finance-tab.md §3.2).
    ALERT_FIRED_TTL = 3600

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
    def api_metrics_minute_key(minute: int) -> str:
        return RedisKeys.API_METRICS_MINUTE.format(minute=minute)

    @staticmethod
    def events_pushed_minute_key(minute: int) -> str:
        return RedisKeys.EVENTS_PUSHED_MINUTE.format(minute=minute)

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

    @staticmethod
    def sse_active_connections_key() -> str:
        return RedisKeys.SSE_ACTIVE_CONNECTIONS

    @staticmethod
    def stream_history_key(category: str) -> str:
        return RedisKeys.STREAM_HISTORY.format(category=category)

    @staticmethod
    def topic_stats_pushed_key(tenant_id: str) -> str:
        return RedisKeys.TOPIC_STATS_PUSHED.format(tenant_id=tenant_id)

    @staticmethod
    def alert_fired_key(tenant_id: str, item_id: str) -> str:
        return RedisKeys.ALERT_FIRED.format(tenant_id=tenant_id, item_id=item_id)


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


async def redis_sadd(key: str, *members: str, ttl: int | None = None) -> int:
    client = await get_redis_client()
    added = await client.sadd(key, *members)
    if ttl is not None:
        # Refresh on every write so an active set never expires mid-stream while
        # an abandoned one is reclaimed automatically.
        await client.expire(key, ttl)
    return added


async def redis_sismember(key: str, member: str) -> bool:
    client = await get_redis_client()
    return await client.sismember(key, member)


async def redis_push_history(key: str, value: Any, max_len: int, ttl: int | None = None) -> None:
    """Append an event to a capped list (newest first). Never raises: failures
    are logged so a broken Redis cannot interrupt the live publish path."""
    try:
        client = await get_redis_client()
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        pipe = client.pipeline(transaction=False)
        pipe.lpush(key, value)
        pipe.ltrim(key, 0, max_len - 1)
        if ttl is not None:
            pipe.expire(key, ttl)
        await pipe.execute()
    except Exception as e:
        logger.warning(f"Redis history push failed for {key}: {e}")


async def redis_lrange(key: str, start: int = 0, end: int = -1) -> list[str]:
    """Read a slice of a list without raising: errors are logged and an empty
    list is returned so callers can degrade to "nothing to replay"."""
    try:
        client = await get_redis_client()
        return await client.lrange(key, start, end)
    except Exception as e:
        logger.warning(f"Redis lrange failed for {key}: {e}")
        return []
