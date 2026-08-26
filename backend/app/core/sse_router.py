import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from enum import StrEnum

from app.core.redis import (
    RedisKeys,
    get_redis_client,
    redis_lrange,
    redis_publish,
    redis_push_history,
)

logger = logging.getLogger(__name__)


async def _record_push_event_count() -> None:
    """Count one pushed SSE event into a Redis minute bucket.

    Single non-transactional pipeline, single round trip, fire-and-forget —
    no exception may ever reach the publish path (mirrors
    core/middleware.py::_record_request_stats). Reader side:
    DashboardService._events_pushed_window() sums the sliding 60-minute window
    for GET /dashboard/business-metrics (dashboard-tab.md §3.5).
    """
    try:
        client = await get_redis_client()
        minute_key = RedisKeys.events_pushed_minute_key(int(time.time()) // 60)
        pipe = client.pipeline(transaction=False)
        pipe.incr(minute_key)
        pipe.expire(minute_key, RedisKeys.EVENTS_PUSHED_MINUTE_TTL)
        await pipe.execute()
    except Exception as e:
        logger.debug(f"Failed to record SSE push event count: {e}")


async def _sync_active_connections_gauge() -> None:
    """Write the in-router active SSE connection count to Redis as a gauge.

    The scheduler (a separate worker process in prod) cannot see this process's
    connection registry, so the count is exposed through the Redis key
    `sse:active_connections` and consumed by scheduler/manager.py's
    evaluate_load_multiplier (finance-tab.md §3.8.3). Simple SET-of-full-count
    with a TTL rather than INCR/DECR: every write is self-correcting (no drift
    from lost decrements), and the TTL plus the heartbeat refresh guarantee the
    gauge disappears shortly after the api process dies instead of throttling
    collection forever on a stale number. Single api process assumption — with
    multiple api replicas each would overwrite the others' count (same
    limitation as the in-process registry itself, see dashboard-tab.md §3.8).

    Refresh points: register()/unregister() for immediacy, the heartbeat loop
    for TTL keep-alive between connection changes. Fire-and-forget: Redis
    failures are logged and swallowed, never reaching the connect/disconnect path.
    """
    try:
        client = await get_redis_client()
        count = event_router.get_connections_count()
        await client.set(
            RedisKeys.sse_active_connections_key(),
            count,
            ex=RedisKeys.SSE_ACTIVE_CONNECTIONS_TTL,
        )
    except Exception as e:
        logger.debug(f"Failed to sync SSE active connections gauge: {e}")


async def _signal_first_subscriber_resume() -> None:
    """The SSE subscriber registry just went 0 → 1: resume paused collection.

    Reverses the no-subscriber adaptive pause (scheduler/manager.py
    adaptive_reschedule). Two independent delivery paths, both idempotent
    no-ops when nothing is paused:
      - Dev (SCHEDULER_ENABLED=true): the scheduler runs in this process, so
        resume the embedded scheduler directly (deferred import — the manager
        imports this module at top level, so importing it back at module load
        time would be circular).
      - Always: publish `scheduler_resume` on channel:dashboard. This is the
        prod delivery path (the scheduler lives in the worker process, whose
        source_event_listener dispatches the event to resume_paused_jobs());
        publishing it in dev as well is harmless and covers mixed setups.
    Never raises: the connect path must not depend on the resume signal.
    """
    from app.config import settings

    try:
        if settings.scheduler_enabled:
            from app.scheduler.manager import scheduler_manager

            resumed = await scheduler_manager.resume_paused_jobs()
            logger.info(f"First SSE subscriber: resumed {resumed} paused job(s) in embedded scheduler")
    except Exception as e:
        logger.debug(f"Embedded scheduler resume on first subscriber failed: {e}")

    try:
        await redis_publish(
            RedisKeys.channel_key("dashboard"),
            {
                "event": "scheduler_resume",
                "reason": "first_sse_subscriber",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
    except Exception as e:
        logger.debug(f"Failed to publish scheduler_resume event: {e}")


def _spawn_background_hook(coro) -> None:
    """Fire-and-forget a background coroutine from sync router methods.

    register()/unregister() are synchronous; the gauge/resume side effects are
    async and must never block or break them. When no event loop is running
    (sync unit tests), the coroutine is closed and silently dropped. Spawned
    tasks are short-lived and exception-safe by construction.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    loop.create_task(coro)


class SSEEventType(StrEnum):
    ITEM_UPDATE = "item_update"
    QUOTE_UPDATE = "quote_update"
    MARKET_INDEX_UPDATE = "market_index_update"
    NAV_ESTIMATE_UPDATE = "nav_estimate_update"
    COMMODITY_UPDATE = "commodity_update"
    SYSTEM_METRIC_UPDATE = "system_metric_update"
    SOURCE_HEALTH_UPDATE = "source_health_update"
    TOPIC_STATS_UPDATE = "topic_stats_update"
    HEARTBEAT = "heartbeat"


class SSEConnection:
    def __init__(self, client_id: str, categories: list[str], tenant_id: str, user_id: str | None = None):
        self.client_id = client_id
        self.categories = categories
        self.tenant_id = tenant_id
        # Keep the owning user so the disconnect audit can match the exact
        # sse_connections row (the in-memory registry is keyed by client_id,
        # which is not stored in the DB row).
        self.user_id = user_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.is_active = True
        self.connected_at = datetime.now(UTC)
        self.last_event_at: datetime | None = None
        self.events_sent_count: int = 0

    async def send_event(self, event_type: str, data: dict | list):
        try:
            await self.queue.put({"event_type": event_type, "data": data})
            self.last_event_at = datetime.now(UTC)
            self.events_sent_count += 1
        except asyncio.QueueFull:
            logger.warning(f"SSE queue full for {self.client_id}, dropping event")


class SSEEventRouter:
    _connections: dict[str, SSEConnection] = {}
    _subscriptions: dict[str, set[str]] = {}
    _heartbeat_task: asyncio.Task | None = None
    _redis_listener_task: asyncio.Task | None = None
    _event_counter: int = 0

    def register(
        self,
        client_id: str,
        categories: list[str],
        tenant_id: str,
        user_id: str | None = None,
    ) -> SSEConnection:
        # Capture this BEFORE inserting the new connection so a 0 → 1 registry
        # transition (first subscriber) is detectable; the resume hook reverses
        # the no-subscriber adaptive pause (see _signal_first_subscriber_resume).
        was_empty = self.get_connections_count() == 0

        conn = SSEConnection(client_id, categories, tenant_id, user_id=user_id)
        self._connections[client_id] = conn
        for category in categories:
            self._subscriptions.setdefault(category, set()).add(client_id)
        logger.info(f"SSE connection registered: {client_id} categories={categories}")

        # System-level side effects — the cross-process load gauge and the
        # first-subscriber resume hook — are properties of THE process-wide
        # registry (the module singleton), so they only fire for it. Local
        # instances (notably unit tests) stay side-effect free.
        if self is event_router:
            _spawn_background_hook(_sync_active_connections_gauge())
            if was_empty:
                _spawn_background_hook(_signal_first_subscriber_resume())
        return conn

    def unregister(self, client_id: str) -> SSEConnection | None:
        conn = self._connections.pop(client_id, None)
        if conn:
            conn.is_active = False
            for category in conn.categories:
                subscribers = self._subscriptions.get(category)
                if subscribers:
                    subscribers.discard(client_id)
                    if not subscribers:
                        del self._subscriptions[category]
            logger.info(f"SSE connection unregistered: {client_id} events_sent={conn.events_sent_count}")
            # Same rationale as in register(): only the process-wide registry
            # drives the cross-process gauge.
            if self is event_router:
                _spawn_background_hook(_sync_active_connections_gauge())
        return conn

    def get_connection(self, client_id: str) -> SSEConnection | None:
        return self._connections.get(client_id)

    def get_connections_count(self) -> int:
        return sum(1 for c in self._connections.values() if c.is_active)

    def get_connections_by_category(self, category: str) -> list[SSEConnection]:
        client_ids = self._subscriptions.get(category, set())
        connections = []
        for cid in client_ids:
            conn = self._connections.get(cid)
            if conn and conn.is_active:
                connections.append(conn)
        return connections

    def get_all_active_connections(self) -> list[SSEConnection]:
        return [c for c in self._connections.values() if c.is_active]

    def _generate_event_id(self) -> str:
        self._event_counter += 1
        return f"{int(time.time())}-{self._event_counter}"

    async def push_event(self, category: str, event_type: SSEEventType, data: dict | list, tenant_id: str):
        message = {
            "event_type": event_type.value if isinstance(event_type, SSEEventType) else event_type,
            "channel": category,
            "data": data,
            "tenant_id": tenant_id,
            "event_id": self._generate_event_id(),
            "published_at": datetime.now(UTC).isoformat(),
        }
        channel_key = RedisKeys.channel_key(category)
        try:
            # Persist the full message (including event_id) before publishing so a
            # reconnecting client can replay what it missed via Last-Event-ID.
            # The degraded (Redis-down) fallback path below deliberately skips the
            # history write: an in-process direct push cannot be replayed.
            await redis_push_history(
                RedisKeys.stream_history_key(category),
                message,
                RedisKeys.STREAM_HISTORY_LIMIT,
                RedisKeys.STREAM_HISTORY_TTL,
            )
            await redis_publish(channel_key, message)
        except Exception as e:
            logger.warning(f"Redis Pub/Sub publish failed for {channel_key}: {e}, falling back to direct push")

        # Business-metrics counter (dashboard-tab.md §3.5 "SSE推送事件数(1h)"):
        # count every push_event regardless of publish success; failures inside
        # the helper are only logged and never break the live push.
        await _record_push_event_count()

        await self._on_redis_message(channel_key, message)

    async def get_missed_events(
        self,
        category: str,
        last_event_id: str,
        tenant_id: str | None = None,
    ) -> list[dict]:
        """Return the events published on `category` after `last_event_id`.

        Reads the per-channel history list (newest first), reverses it back to
        chronological order, locates the anchor event and returns everything
        after it, filtered to the requesting tenant. A missing anchor (history
        expired/rotated) or any Redis error yields an empty list so the caller
        can silently skip the replay.
        """
        try:
            raw_events = await redis_lrange(RedisKeys.stream_history_key(category), start=0, end=-1)
        except Exception as e:
            logger.debug(f"Failed to read SSE history for {category}: {e}")
            return []

        if not raw_events:
            return []

        events: list[dict] = []
        for raw in reversed(raw_events):
            try:
                events.append(json.loads(raw))
            except (TypeError, json.JSONDecodeError):
                continue

        anchor_index = None
        for idx, event in enumerate(events):
            if event.get("event_id") == last_event_id:
                anchor_index = idx
                break

        if anchor_index is None:
            logger.debug(f"SSE replay anchor {last_event_id} not found in history for {category}")
            return []

        missed = []
        for event in events[anchor_index + 1 :]:
            event_tenant = event.get("tenant_id")
            if tenant_id and event_tenant and event_tenant != tenant_id:
                continue
            missed.append(event)
        return missed

    async def push_to_client(self, client_id: str, event_type: SSEEventType, data: dict | list):
        conn = self._connections.get(client_id)
        if conn and conn.is_active:
            event_type_str = event_type.value if isinstance(event_type, SSEEventType) else event_type
            await conn.send_event(event_type_str, data)

    async def _on_redis_message(self, channel: str, event_data: dict):
        category = channel.replace("channel:", "")
        tenant_id = event_data.get("tenant_id")
        event_type = event_data.get("event_type", "item_update")
        data = event_data.get("data", {})

        subscribers = self._subscriptions.get(category, set())
        for client_id in subscribers.copy():
            conn = self._connections.get(client_id)
            if conn and conn.is_active:
                if tenant_id and conn.tenant_id != tenant_id:
                    continue
                await conn.send_event(event_type, data)

        all_subscribers = self._subscriptions.get("all", set())
        for client_id in all_subscribers.copy():
            conn = self._connections.get(client_id)
            if conn and conn.is_active:
                if tenant_id and conn.tenant_id != tenant_id:
                    continue
                if client_id not in subscribers:
                    await conn.send_event(event_type, data)

    async def start_redis_listener(self):
        from app.core.redis import get_redis_client

        client = await get_redis_client()
        pubsub = client.pubsub()
        channels = [
            RedisKeys.channel_key("finance"),
            RedisKeys.channel_key("tech"),
            RedisKeys.channel_key("dashboard"),
            RedisKeys.channel_key("admin"),
            RedisKeys.channel_key("all"),
        ]
        await pubsub.subscribe(*channels)
        logger.info(f"Redis Pub/Sub listener started for channels: {channels}")

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    channel = message["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode("utf-8")
                    try:
                        data = json.loads(message["data"])
                        await self._on_redis_message(channel, data)
                    except json.JSONDecodeError:
                        logger.warning(f"Invalid JSON in Pub/Sub message on {channel}")
        except asyncio.CancelledError:
            logger.info("Redis Pub/Sub listener cancelled")
        except Exception as e:
            logger.error(f"Redis Pub/Sub listener error: {e}")
        finally:
            await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    def start_heartbeat(self, interval: int = 30) -> asyncio.Task:
        async def _heartbeat_loop():
            logger.info(f"SSE heartbeat started with interval {interval}s")
            while True:
                try:
                    await asyncio.sleep(interval)
                    heartbeat_data = {"timestamp": datetime.now(UTC).isoformat()}
                    for conn in self.get_all_active_connections():
                        with contextlib.suppress(Exception):
                            await conn.send_event(SSEEventType.HEARTBEAT.value, heartbeat_data)
                    # Keep the Redis connection gauge alive between connect/disconnect
                    # events: the key's TTL (60s) is 2x this loop's default cadence, so
                    # one refresh per cycle is enough and a dead api process still lets
                    # the gauge expire promptly (degraded → normal collection frequency).
                    await _sync_active_connections_gauge()
                except asyncio.CancelledError:
                    logger.info("SSE heartbeat cancelled")
                    break
                except Exception as e:
                    logger.error(f"Heartbeat error: {e}")

        self._heartbeat_task = asyncio.create_task(_heartbeat_loop())
        return self._heartbeat_task

    def stop_heartbeat(self):
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
            logger.info("SSE heartbeat stopped")

    def stop_redis_listener(self):
        if self._redis_listener_task and not self._redis_listener_task.done():
            self._redis_listener_task.cancel()
            self._redis_listener_task = None
            logger.info("Redis Pub/Sub listener stopped")

    def get_stats(self) -> dict:
        active = self.get_all_active_connections()
        connections_by_category = {}
        for category, subscribers in self._subscriptions.items():
            connections_by_category[category] = sum(
                1 for cid in subscribers if self._connections.get(cid) and self._connections[cid].is_active
            )
        total_events = sum(c.events_sent_count for c in active)
        avg_connection_duration = 0
        if active:
            now = datetime.now(UTC)
            durations = [(now - c.connected_at).total_seconds() for c in active]
            avg_connection_duration = sum(durations) / len(durations)

        return {
            "total_connections": len(active),
            "connections_by_channel": connections_by_category,
            "total_events_pushed": total_events,
            "avg_connection_duration_seconds": round(avg_connection_duration, 1),
        }


event_router = SSEEventRouter()
