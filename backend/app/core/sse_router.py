import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from enum import StrEnum

from app.core.redis import RedisKeys, redis_publish

logger = logging.getLogger(__name__)


class SSEEventType(StrEnum):
    ITEM_UPDATE = "item_update"
    QUOTE_UPDATE = "quote_update"
    MARKET_INDEX_UPDATE = "market_index_update"
    NAV_ESTIMATE_UPDATE = "nav_estimate_update"
    COMMODITY_UPDATE = "commodity_update"
    SYSTEM_METRIC_UPDATE = "system_metric_update"
    SOURCE_HEALTH_UPDATE = "source_health_update"
    HEARTBEAT = "heartbeat"


class SSEConnection:
    def __init__(self, client_id: str, categories: list[str], tenant_id: str):
        self.client_id = client_id
        self.categories = categories
        self.tenant_id = tenant_id
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self.is_active = True
        self.connected_at = datetime.now(UTC)
        self.last_event_at: datetime | None = None
        self.events_sent_count: int = 0

    async def send_event(self, event_type: str, data: dict):
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

    def register(self, client_id: str, categories: list[str], tenant_id: str) -> SSEConnection:
        conn = SSEConnection(client_id, categories, tenant_id)
        self._connections[client_id] = conn
        for category in categories:
            self._subscriptions.setdefault(category, set()).add(client_id)
        logger.info(f"SSE connection registered: {client_id} categories={categories}")
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

    async def push_event(self, category: str, event_type: SSEEventType, data: dict, tenant_id: str):
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
            await redis_publish(channel_key, message)
        except Exception as e:
            logger.warning(f"Redis Pub/Sub publish failed for {channel_key}: {e}, falling back to direct push")

        await self._on_redis_message(channel_key, message)

    async def push_to_client(self, client_id: str, event_type: SSEEventType, data: dict):
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
