import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.sse_router import (
    SSEConnection,
    SSEEventRouter,
    SSEEventType,
    event_router,
)


class TestSSEEventType:
    def test_item_update(self):
        assert SSEEventType.ITEM_UPDATE == "item_update"

    def test_quote_update(self):
        assert SSEEventType.QUOTE_UPDATE == "quote_update"

    def test_market_index_update(self):
        assert SSEEventType.MARKET_INDEX_UPDATE == "market_index_update"

    def test_nav_estimate_update(self):
        assert SSEEventType.NAV_ESTIMATE_UPDATE == "nav_estimate_update"

    def test_commodity_update(self):
        assert SSEEventType.COMMODITY_UPDATE == "commodity_update"

    def test_system_metric_update(self):
        assert SSEEventType.SYSTEM_METRIC_UPDATE == "system_metric_update"

    def test_source_health_update(self):
        assert SSEEventType.SOURCE_HEALTH_UPDATE == "source_health_update"

    def test_heartbeat(self):
        assert SSEEventType.HEARTBEAT == "heartbeat"


class TestSSEConnection:
    def test_init(self):
        conn = SSEConnection(
            client_id="client1",
            categories=["finance", "tech"],
            tenant_id="tenant1"
        )
        assert conn.client_id == "client1"
        assert conn.categories == ["finance", "tech"]
        assert conn.tenant_id == "tenant1"
        assert conn.is_active is True
        assert conn.events_sent_count == 0
        assert conn.last_event_at is None

    async def test_send_event(self):
        conn = SSEConnection("client1", ["finance"], "tenant1")
        await conn.send_event("item_update", {"msg": "test"})
        assert conn.events_sent_count == 1
        assert conn.last_event_at is not None
        event = await conn.queue.get()
        assert event["event_type"] == "item_update"
        assert event["data"]["msg"] == "test"

    async def test_queue_full_handling(self):
        conn = SSEConnection("client1", ["finance"], "tenant1")
        mock_queue = MagicMock(spec=asyncio.Queue)
        mock_queue.put = AsyncMock(side_effect=asyncio.QueueFull)
        conn.queue = mock_queue

        with patch("app.core.sse_router.logger") as mock_logger:
            await conn.send_event("event2", {"msg": "test"})
            mock_logger.warning.assert_called_once()


class TestSSEEventRouter:
    def setup_method(self):
        self.router = SSEEventRouter()
        self.router._connections = {}
        self.router._subscriptions = {}

    def test_register(self):
        conn = self.router.register("client1", ["finance", "tech"], "tenant1")
        assert conn.client_id == "client1"
        assert "client1" in self.router._connections
        assert "client1" in self.router._subscriptions["finance"]
        assert "client1" in self.router._subscriptions["tech"]

    def test_unregister(self):
        self.router.register("client1", ["finance"], "tenant1")
        conn = self.router.unregister("client1")
        assert conn is not None
        assert conn.is_active is False
        assert "client1" not in self.router._connections
        assert "finance" not in self.router._subscriptions

    def test_unregister_nonexistent(self):
        conn = self.router.unregister("nonexistent")
        assert conn is None

    def test_get_connection(self):
        self.router.register("client1", ["finance"], "tenant1")
        conn = self.router.get_connection("client1")
        assert conn is not None
        assert conn.client_id == "client1"

    def test_get_connection_nonexistent(self):
        conn = self.router.get_connection("nonexistent")
        assert conn is None

    def test_get_connections_count(self):
        self.router.register("client1", ["finance"], "tenant1")
        self.router.register("client2", ["tech"], "tenant1")
        assert self.router.get_connections_count() == 2

    def test_get_connections_count_excludes_inactive(self):
        self.router.register("client1", ["finance"], "tenant1")
        conn2 = self.router.register("client2", ["tech"], "tenant1")
        conn2.is_active = False
        assert self.router.get_connections_count() == 1

    def test_get_connections_by_category(self):
        self.router.register("client1", ["finance"], "tenant1")
        self.router.register("client2", ["finance", "tech"], "tenant1")

        finance_conns = self.router.get_connections_by_category("finance")
        assert len(finance_conns) == 2

        tech_conns = self.router.get_connections_by_category("tech")
        assert len(tech_conns) == 1

    def test_get_connections_by_category_excludes_inactive(self):
        self.router.register("client1", ["finance"], "tenant1")
        conn2 = self.router.register("client2", ["finance"], "tenant1")
        conn2.is_active = False

        finance_conns = self.router.get_connections_by_category("finance")
        assert len(finance_conns) == 1

    def test_get_all_active_connections(self):
        self.router.register("client1", ["finance"], "tenant1")
        self.router.register("client2", ["tech"], "tenant1")

        all_conns = self.router.get_all_active_connections()
        assert len(all_conns) == 2

    def test_generate_event_id(self):
        id1 = self.router._generate_event_id()
        id2 = self.router._generate_event_id()
        assert id1 != id2
        assert "-" in id1

    async def test_push_event(self):
        self.router.register("client1", ["finance"], "tenant1")

        with patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish:
            await self.router.push_event("finance", SSEEventType.ITEM_UPDATE, {"msg": "test"}, "tenant1")
            mock_publish.assert_called_once()

    async def test_push_event_redis_failure(self):
        self.router.register("client1", ["finance"], "tenant1")

        with patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish:
            mock_publish.side_effect = Exception("Redis down")
            with patch("app.core.sse_router.logger") as mock_logger:
                await self.router.push_event("finance", SSEEventType.ITEM_UPDATE, {"msg": "test"}, "tenant1")
                mock_logger.warning.assert_called()

    async def test_push_to_client(self):
        conn = self.router.register("client1", ["finance"], "tenant1")
        await self.router.push_to_client("client1", SSEEventType.HEARTBEAT, {"ts": "now"})
        assert conn.events_sent_count == 1

    async def test_push_to_client_no_connection(self):
        await self.router.push_to_client("nonexistent", SSEEventType.HEARTBEAT, {"ts": "now"})

    async def test_on_redis_message_tenant_filtering(self):
        conn1 = self.router.register("client1", ["finance"], "tenant1")
        conn2 = self.router.register("client2", ["finance"], "tenant2")

        message = {
            "event_type": "item_update",
            "data": {"msg": "test"},
            "tenant_id": "tenant1"
        }
        await self.router._on_redis_message("channel:finance", message)

        assert conn1.events_sent_count == 1
        assert conn2.events_sent_count == 0

    async def test_on_redis_message_all_channel(self):
        conn1 = self.router.register("client1", ["all"], "tenant1")

        message = {
            "event_type": "system_metric_update",
            "data": {"metric": "cpu"},
            "tenant_id": "tenant1"
        }
        await self.router._on_redis_message("channel:all", message)
        assert conn1.events_sent_count == 1

    async def test_on_redis_message_all_channel_tenant_filter(self):
        conn_other = self.router.register("client_other", ["all"], "other_tenant")

        message = {
            "event_type": "system_metric_update",
            "data": {"metric": "cpu"},
            "tenant_id": "tenant1"
        }
        await self.router._on_redis_message("channel:all", message)
        assert conn_other.events_sent_count == 0

    async def test_on_redis_message_all_channel_not_duplicate(self):
        conn = self.router.register("client1", ["finance", "all"], "tenant1")

        message = {
            "event_type": "item_update",
            "data": {"msg": "test"},
            "tenant_id": "tenant1"
        }
        await self.router._on_redis_message("channel:finance", message)
        assert conn.events_sent_count == 1

    async def test_on_redis_message_inactive_conn_ignored(self):
        conn = self.router.register("client1", ["finance"], "tenant1")
        conn.is_active = False

        message = {
            "event_type": "item_update",
            "data": {},
            "tenant_id": "tenant1"
        }
        await self.router._on_redis_message("channel:finance", message)
        assert conn.events_sent_count == 0

    async def test_start_redis_listener_cancelled(self):
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {"type": "message", "channel": "channel:finance", "data": '{"test": 1}'}
            raise asyncio.CancelledError()

        mock_pubsub.listen = mock_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.pubsub.return_value = mock_pubsub

        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=mock_client):
            await self.router.start_redis_listener()
            mock_pubsub.subscribe.assert_called_once()
            mock_pubsub.unsubscribe.assert_called_once()
            mock_pubsub.aclose.assert_called_once()

    async def test_start_redis_listener_invalid_json(self):
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {"type": "message", "channel": "channel:finance", "data": "not json"}
            raise asyncio.CancelledError()

        mock_pubsub.listen = mock_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.pubsub.return_value = mock_pubsub

        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=mock_client):
            await self.router.start_redis_listener()

    async def test_start_redis_listener_bytes_channel(self):
        mock_pubsub = AsyncMock()

        async def mock_listen():
            yield {"type": "message", "channel": b"channel:finance", "data": '{"event_type": "item_update", "data": {}, "tenant_id": "t1"}'}
            raise asyncio.CancelledError()

        mock_pubsub.listen = mock_listen
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.pubsub.return_value = mock_pubsub

        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=mock_client):
            await self.router.start_redis_listener()

    async def test_start_redis_listener_exception(self):
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock(side_effect=Exception("Connection failed"))
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()

        mock_client = MagicMock()
        mock_client.pubsub.return_value = mock_pubsub

        with patch("app.core.redis.get_redis_client", new_callable=AsyncMock, return_value=mock_client):
            with pytest.raises(Exception, match="Connection failed"):
                await self.router.start_redis_listener()
            # subscribe fails before the try/except block, so unsubscribe is not called
            mock_pubsub.subscribe.assert_called_once()

    def test_start_heartbeat(self):
        with patch("asyncio.create_task") as mock_task:
            self.router.start_heartbeat(interval=30)
            mock_task.assert_called_once()

    async def test_heartbeat_loop_sends_events(self):
        self.router.register("client1", ["finance"], "tenant1")
        self.router.start_heartbeat(interval=0)
        await asyncio.sleep(0.05)
        self.router.stop_heartbeat()
        conn = self.router.get_connection("client1")
        assert conn.events_sent_count >= 1

    def test_stop_redis_listener(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        self.router._redis_listener_task = mock_task

        self.router.stop_redis_listener()
        mock_task.cancel.assert_called_once()
        assert self.router._redis_listener_task is None

    def test_stop_redis_listener_none(self):
        self.router._redis_listener_task = None
        self.router.stop_redis_listener()
        assert self.router._redis_listener_task is None

    def test_stop_redis_listener_done(self):
        mock_task = MagicMock()
        mock_task.done.return_value = True
        self.router._redis_listener_task = mock_task

        self.router.stop_redis_listener()
        mock_task.cancel.assert_not_called()

    def test_stop_heartbeat(self):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        self.router._heartbeat_task = mock_task

        self.router.stop_heartbeat()
        mock_task.cancel.assert_called_once()
        assert self.router._heartbeat_task is None

    def test_stop_heartbeat_no_task(self):
        self.router._heartbeat_task = None
        self.router.stop_heartbeat()

    def test_stop_heartbeat_done_task(self):
        mock_task = MagicMock()
        mock_task.done.return_value = True
        self.router._heartbeat_task = mock_task

        self.router.stop_heartbeat()
        mock_task.cancel.assert_not_called()

    def test_get_stats_empty(self):
        stats = self.router.get_stats()
        assert stats["total_connections"] == 0
        assert stats["connections_by_channel"] == {}
        assert stats["total_events_pushed"] == 0

    def test_get_stats_with_connections(self):
        self.router.register("client1", ["finance"], "tenant1")
        self.router.register("client2", ["tech"], "tenant1")

        stats = self.router.get_stats()
        assert stats["total_connections"] == 2
        assert "finance" in stats["connections_by_channel"]
        assert "tech" in stats["connections_by_channel"]


class TestEventRouterSingleton:
    def test_singleton_instance(self):
        assert event_router is not None
        assert isinstance(event_router, SSEEventRouter)
