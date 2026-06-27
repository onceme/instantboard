import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.sse import SSEService


class TestConnect:
    @patch("app.services.sse.event_router")
    async def test_connect_success(self, mock_router):
        conn = MagicMock()
        conn.connected_at = datetime.now(UTC)
        mock_router.register.return_value = conn

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        service = SSEService()
        result = await service.connect(
            client_id="client-1",
            categories=["finance", "tech"],
            tenant_id="tenant-1",
            user_id="user-1",
            db_session=db,
            client_ip="127.0.0.1",
            user_agent="Mozilla/5.0",
        )

        assert result["client_id"] == "client-1"
        assert result["categories"] == ["finance", "tech"]
        assert result["tenant_id"] == "tenant-1"
        mock_router.register.assert_called_once()
        db.add.assert_called_once()
        db.commit.assert_called_once()


class TestDisconnect:
    @patch("app.services.sse.event_router")
    async def test_disconnect_success(self, mock_router):
        conn = MagicMock()
        conn.tenant_id = "tenant-1"
        conn.events_sent_count = 10
        conn.connected_at = datetime.now(UTC)
        mock_router.unregister.return_value = conn

        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()

        service = SSEService()
        result = await service.disconnect("client-1", db)

        assert result is not None
        assert result["client_id"] == "client-1"
        assert result["events_sent"] == 10
        mock_router.unregister.assert_called_once_with("client-1")

    @patch("app.services.sse.event_router")
    async def test_disconnect_not_found(self, mock_router):
        mock_router.unregister.return_value = None

        db = AsyncMock()

        service = SSEService()
        result = await service.disconnect("unknown-client", db)
        assert result is None


class TestGetConnectionStatus:
    @patch("app.services.sse.event_router")
    async def test_get_status_active(self, mock_router):
        conn = MagicMock()
        conn.client_id = "client-1"
        conn.categories = ["finance"]
        conn.tenant_id = "tenant-1"
        conn.is_active = True
        conn.connected_at = datetime.now(UTC)
        conn.last_event_at = datetime.now(UTC)
        conn.events_sent_count = 5
        mock_router.get_connection.return_value = conn

        service = SSEService()
        result = await service.get_connection_status("client-1")

        assert result is not None
        assert result["client_id"] == "client-1"
        assert result["is_active"] is True
        assert result["events_sent"] == 5

    @patch("app.services.sse.event_router")
    async def test_get_status_not_found(self, mock_router):
        mock_router.get_connection.return_value = None

        service = SSEService()
        result = await service.get_connection_status("unknown")
        assert result is None


class TestGetAllConnections:
    @patch("app.services.sse.event_router")
    async def test_get_connections(self, mock_router):
        conn1 = MagicMock()
        conn1.client_id = "c1"
        conn1.categories = ["finance"]
        conn1.connected_at = datetime.now(UTC)
        conn1.events_sent_count = 5
        conn1.tenant_id = "tenant-1"

        conn2 = MagicMock()
        conn2.client_id = "c2"
        conn2.categories = ["tech"]
        conn2.connected_at = datetime.now(UTC)
        conn2.events_sent_count = 3
        conn2.tenant_id = "tenant-1"

        conn3 = MagicMock()
        conn3.tenant_id = "other-tenant"

        mock_router.get_all_active_connections.return_value = [conn1, conn2, conn3]

        service = SSEService()
        result = await service.get_all_connections("tenant-1")
        assert len(result) == 2


class TestGetSSEStats:
    @patch("app.services.sse.event_router")
    async def test_get_stats(self, mock_router):
        mock_router.get_stats.return_value = {
            "total_connections": 10,
            "connections_by_channel": {"finance": 5},
            "total_events_pushed": 100,
            "avg_connection_duration_seconds": 60,
        }

        service = SSEService()
        result = await service.get_sse_stats()
        assert result["total_connections"] == 10


class TestPublishSourceHealthUpdate:
    @patch("app.services.sse.event_router")
    async def test_publish(self, mock_router):
        mock_router.push_event = AsyncMock()

        service = SSEService()
        await service.publish_source_health_update(
            source_id="src-1",
            status="healthy",
            last_error=None,
            tenant_id="tenant-1",
        )
        mock_router.push_event.assert_called_once()


class TestPublishItemUpdate:
    @patch("app.services.sse.event_router")
    async def test_publish(self, mock_router):
        mock_router.push_event = AsyncMock()

        service = SSEService()
        await service.publish_item_update(
            category="tech",
            item_data={"title": "Test Item"},
            tenant_id="tenant-1",
        )
        mock_router.push_event.assert_called_once()


class TestPublishQuoteUpdate:
    @patch("app.services.sse.event_router")
    async def test_publish(self, mock_router):
        mock_router.push_event = AsyncMock()

        service = SSEService()
        await service.publish_quote_update(
            symbol="AAPL",
            quote_data={"price": 150},
            tenant_id="tenant-1",
        )
        mock_router.push_event.assert_called_once()


class TestPublishMarketIndexUpdate:
    @patch("app.services.sse.event_router")
    async def test_publish(self, mock_router):
        mock_router.push_event = AsyncMock()

        service = SSEService()
        await service.publish_market_index_update(
            indices_data=[{"symbol": "^GSPC"}],
            tenant_id="tenant-1",
        )
        mock_router.push_event.assert_called_once()


class TestPublishCommodityUpdate:
    @patch("app.services.sse.event_router")
    async def test_publish(self, mock_router):
        mock_router.push_event = AsyncMock()

        service = SSEService()
        await service.publish_commodity_update(
            commodities_data=[{"symbol": "GC=F"}],
            tenant_id="tenant-1",
        )
        mock_router.push_event.assert_called_once()
