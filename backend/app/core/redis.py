import json
import logging
from typing import Any
from uuid import uuid4

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
    # OAuth CSRF protection (security.md §3.2): the authorize endpoint stores every
    # issued state with the provider name as value; POST /auth/sso/{provider} verifies
    # presence + provider match and deletes the key right after (single-use). Both
    # sides fail open on Redis outages so logins stay available.
    SSO_STATE = "sso_state:{state_key}"
    # Layer-3 IP blacklist (security.md §3.3): client IPs banned manually via
    # /api/v1/admin/security/ip-blacklist. Enforced at the request edge by
    # IPBlacklistMiddleware, which reads the whole set (SMEMBERS) into an
    # in-process cache for at most settings.ip_blacklist_cache_ttl seconds.
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
    # Upstream budget governance (fund-intraday-nav.md §6): the per-minute request
    # budget counter for one quote/holdings upstream. Minute-window key (approx.
    # sliding window), EX 120 so two windows survive for the pre-switch read.
    QUOTE_BUDGET = "quote_budget:{name}:{minute}"
    # Circuit-breaker state per upstream: {cooldown_until, consecutive_fails} JSON,
    # EX = the cooldown duration. 403/429/5xx/connection-drop escalates; success
    # halves/zeroes it (fund-intraday-nav.md §6.2).
    QUOTE_BREAKER = "quote_breaker:{name}"
    # Real-time fund NAV estimate (fund-intraday-nav.md §3.4): tenant-less global
    # key — the estimate is derived from public market data; tenant isolation stays
    # at the watchlist layer. TTL 12s (4x the 3s cycle) so a stalled job lets the
    # value expire instead of serving a stale estimate.
    FUND_NAV_RT = "fund_nav_rt:{fund_code}"
    # Followed-fund union set (fund-intraday-nav.md §5.2): members are
    # "{tenant_id}:{fund_code}". Global (tenant-less) — the union is computed once
    # across tenants; invalidation on watchlist add/remove deletes the whole key.
    FUND_FOLLOWED_CODES = "fund_followed_codes"
    # Per-fund holdings snapshot + meta cache (fund-intraday-nav.md §7.2 step 6):
    # JSON serialized on miss-from-PG, TTL 1h, bounded by the once-a-day holdings
    # refresh job.
    FUND_HOLDINGS = "fund_holdings:{fund_code}"
    # Downsampled DB-flush throttle (fund-intraday-nav.md §3.4 case 1): SET NX EX
    # FUND_NAV_DB_FLUSH_MIN_GAP bounds fund_nav_estimates writes to ≤1/min/fund.
    FUND_NAV_RT_FLUSH = "fund_nav_rt_flush:{fund_code}"
    # Online-tenant set for the finance channel (fund-intraday-nav.md §8.1): the
    # api process writes the tenants holding an active finance/all SSE connection;
    # the worker reads it to fan out nav_batch_update only to online tenants.
    SSE_CONNECTED_TENANTS_FINANCE = "sse:connected_tenants:finance"

    SEARCH_TTL = 300
    # OAuth state validity window (seconds): long enough to finish a provider
    # round-trip including a slow consent screen, short enough to bound replays.
    SSO_STATE_TTL = 600
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
    # Upstream budget window (seconds): 2x the minute bucket so the current and
    # the previous window both exist for the budget-left / pre-switch reads.
    QUOTE_BUDGET_TTL = 120
    # Real-time fund NAV estimate TTL (seconds): 4x the intraday cycle interval
    # (fund-intraday-nav.md §3.4).
    FUND_NAV_RT_TTL = 12
    # Followed-fund union set TTL (seconds) — short on purpose: a deleted key
    # (watchlist add/remove) or an expiry both rebuild from PG on next read.
    FUND_FOLLOWED_CODES_TTL = 60
    # Per-fund holdings cache TTL (seconds) (fund-intraday-nav.md §7.2 step 6).
    FUND_HOLDINGS_TTL = 3600
    # Online-tenant finance set TTL (seconds): 3x the 30s SSE heartbeat refresh
    # cadence so a live api process never lets it lapse while a dead api process
    # drops the fan-out target promptly (same gauge logic as
    # SSE_ACTIVE_CONNECTIONS_TTL, fund-intraday-nav.md §8.1).
    SSE_CONNECTED_TENANTS_FINANCE_TTL = 90

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

    @staticmethod
    def quote_budget_key(name: str, minute: str) -> str:
        return RedisKeys.QUOTE_BUDGET.format(name=name, minute=minute)

    @staticmethod
    def quote_breaker_key(name: str) -> str:
        return RedisKeys.QUOTE_BREAKER.format(name=name)

    @staticmethod
    def fund_nav_rt_key(fund_code: str) -> str:
        return RedisKeys.FUND_NAV_RT.format(fund_code=fund_code)

    @staticmethod
    def fund_followed_codes_key() -> str:
        return RedisKeys.FUND_FOLLOWED_CODES

    @staticmethod
    def fund_holdings_key(fund_code: str) -> str:
        return RedisKeys.FUND_HOLDINGS.format(fund_code=fund_code)

    @staticmethod
    def fund_nav_rt_flush_key(fund_code: str) -> str:
        return RedisKeys.FUND_NAV_RT_FLUSH.format(fund_code=fund_code)

    @staticmethod
    def sse_connected_tenants_finance_key() -> str:
        return RedisKeys.SSE_CONNECTED_TENANTS_FINANCE


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


async def redis_smembers(key: str) -> set[str]:
    client = await get_redis_client()
    return await client.smembers(key)


async def redis_srem(key: str, *members: str) -> int:
    client = await get_redis_client()
    return await client.srem(key, *members)


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


async def redis_sliding_window_count(key: str, now_ms: int, window_seconds: int, ttl_seconds: int) -> int:
    """Record one event in a sliding-window rate-limit counter (Redis ZSET) and
    return the number of events inside the window, INCLUDING the new one.

    Aggregated into a single non-transactional pipeline = one round trip per
    request instead of four, because this runs on the hot path of every API
    request (RateLimitMiddleware, security.md §3.3 layer 2). The command order
    matters only in that ZADD lands before ZCARD; trimming may run before or
    after the insert with identical results (the new member's score is always
    inside the window):

      ZADD key now member       record this request's timestamp
      ZREMRANGEBYSCORE key 0 (now-window)   drop members that slid out of the
                                window (exclusive upper bound keeps a member
                                exactly `window` old counting for one more pass)
      ZCARD key                 window population used for the limit decision
      EXPIRE key ttl            let idle keys die shortly after their last
                                member leaves the window

    The member is f"{now_ms}:{uuid4().hex}" — score AND member being the bare
    timestamp would let two requests in the same millisecond collide on one
    member (ZADD updates the score instead of adding), silently skipping a
    count precisely under heavy load. Raises on Redis errors; the middleware
    treats any exception as fail-open.
    """
    client = await get_redis_client()
    member = f"{now_ms}:{uuid4().hex}"
    pipe = client.pipeline(transaction=False)
    pipe.zadd(key, {member: now_ms})
    pipe.zremrangebyscore(key, 0, f"({now_ms - window_seconds * 1000}")
    pipe.zcard(key)
    pipe.expire(key, ttl_seconds)
    results = await pipe.execute()
    return int(results[2])
