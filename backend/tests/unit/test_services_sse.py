import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys
from app.core.sse_router import SSEEventType
from app.models.source import SourceHealth
from app.services.sse import SSEService, build_source_health_update_payload


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
        # user_id must reach the in-memory registry so disconnect() can audit the row
        mock_router.register.assert_called_once_with("client-1", ["finance", "tech"], "tenant-1", user_id="user-1")
        db_conn = db.add.call_args.args[0]
        # The DB row must reuse the registry timestamp so disconnect can match exactly
        assert db_conn.connected_at == conn.connected_at
        assert db_conn.user_id == "user-1"
        db.commit.assert_called_once()


class TestDisconnect:
    @patch("app.services.sse.event_router")
    async def test_disconnect_success(self, mock_router):
        conn = MagicMock()
        conn.tenant_id = "tenant-1"
        conn.user_id = "user-1"
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
    async def test_disconnect_audit_matches_connection_row(self, mock_router):
        """Regression: the disconnect UPDATE used WHERE user_id == conn.tenant_id, which
        never matched, so sse_connections rows were never audited as disconnected.
        The WHERE clause must use the connection's own user_id and connected_at."""
        conn = MagicMock()
        conn.tenant_id = "tenant-1"
        conn.user_id = str(uuid.uuid4())
        conn.events_sent_count = 3
        conn.connected_at = datetime.now(UTC)
        mock_router.unregister.return_value = conn

        db = AsyncMock()
        db.execute = AsyncMock()
        db.commit = AsyncMock()

        service = SSEService()
        await service.disconnect("client-1", db)

        db.execute.assert_awaited_once()
        stmt = db.execute.await_args.args[0]
        params = stmt.compile().params
        assert conn.user_id in params.values()
        assert conn.connected_at in params.values()
        # tenant id must NOT be bound as the user id (the old bug)
        assert conn.tenant_id not in params.values()
        db.commit.assert_awaited_once()

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


def _make_source() -> MagicMock:
    source = MagicMock()
    source.id = uuid.uuid4()
    source.name = "Hacker News"
    source.source_type = "rss"
    return source


def _make_health(**overrides) -> SourceHealth:
    health = SourceHealth(
        source_id=uuid.uuid4(),
        status="down",
        last_success_at=datetime(2026, 8, 11, 8, 0, tzinfo=UTC),
        last_failure_at=datetime(2026, 8, 11, 8, 5, tzinfo=UTC),
        last_error_message="connection timeout",
        consecutive_failures=12,
        total_fetches_24h=20,
        success_count_24h=8,
        avg_response_time_ms=450,
    )
    for key, value in overrides.items():
        setattr(health, key, value)
    return health


class TestBuildSourceHealthUpdatePayload:
    """Contract tests: docs/dev-guide/design/data-flow.md §3.5.4."""

    def test_payload_carries_full_row_state(self):
        source = _make_source()
        health = _make_health()

        payload = build_source_health_update_payload(source, health, "degraded")

        assert payload["source_id"] == str(source.id)
        assert payload["name"] == "Hacker News"
        assert payload["source_type"] == "rss"
        assert payload["status"] == "down"
        assert payload["previous_status"] == "degraded"
        assert payload["last_error"] == "connection timeout"
        assert payload["last_success_at"] == "2026-08-11T08:00:00+00:00"
        assert payload["last_failure_at"] == "2026-08-11T08:05:00+00:00"
        assert payload["avg_response_time_ms"] == 450
        assert payload["consecutive_failures"] == 12
        assert payload["success_count_24h"] == 8
        assert payload["total_fetches_24h"] == 20
        assert payload["success_rate_24h"] == pytest.approx(8 / 20)
        # Publish time must be a parseable ISO-8601 timestamp
        datetime.fromisoformat(payload["timestamp"])

    def test_payload_includes_every_frontend_table_field(self):
        # Regression: the old minimal payload ({source_id, status, last_error,
        # timestamp}) lacked every field rendered by DataSourcesHealth.vue
        # (last_success_at / last_failure_at / avg_response_time_ms / ...), so
        # the dashboard health table could never refresh from the SSE event.
        source = _make_source()
        health = _make_health()

        payload = build_source_health_update_payload(source, health, "degraded")

        required = {
            "source_id",
            "name",
            "source_type",
            "status",
            "previous_status",
            "last_error",
            "last_success_at",
            "last_failure_at",
            "avg_response_time_ms",
            "consecutive_failures",
            "success_count_24h",
            "total_fetches_24h",
            "success_rate_24h",
            "timestamp",
        }
        assert required <= payload.keys()

    def test_payload_null_times_and_zero_fetches(self):
        source = _make_source()
        health = _make_health(
            last_success_at=None,
            last_failure_at=None,
            last_error_message=None,
            total_fetches_24h=0,
            success_count_24h=0,
        )

        payload = build_source_health_update_payload(source, health, "healthy")

        assert payload["last_success_at"] is None
        assert payload["last_failure_at"] is None
        assert payload["last_error"] is None
        assert payload["success_rate_24h"] is None


class TestPublishSourceHealthUpdate:
    @patch("app.services.sse.event_router")
    async def test_publish_pushes_contract_payload_on_dashboard_channel(self, mock_router):
        mock_router.push_event = AsyncMock()
        payload = build_source_health_update_payload(_make_source(), _make_health(), "degraded")

        service = SSEService()
        await service.publish_source_health_update(payload, tenant_id="tenant-1")

        mock_router.push_event.assert_awaited_once_with(
            category="dashboard",
            event_type=SSEEventType.SOURCE_HEALTH_UPDATE,
            data=payload,
            tenant_id="tenant-1",
        )

    @patch("app.services.sse.event_router")
    async def test_publish_defaults_to_system_tenant(self, mock_router):
        # System-tenant scoping is what lets admin sessions (whose JWT tenant
        # claim is the system tenant) receive health events of system sources.
        mock_router.push_event = AsyncMock()

        service = SSEService()
        await service.publish_source_health_update({"source_id": "src-1", "status": "healthy"})

        mock_router.push_event.assert_awaited_once_with(
            category="dashboard",
            event_type=SSEEventType.SOURCE_HEALTH_UPDATE,
            data={"source_id": "src-1", "status": "healthy"},
            tenant_id=str(SYSTEM_TENANT_ID),
        )


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


class _FakeRedis:
    """In-memory Redis stand-in with SET NX/EX semantics and a manual clock."""

    def __init__(self):
        self._data = {}
        self._expiry = {}
        self._clock = 0.0

    def advance(self, seconds):
        self._clock += seconds

    def _purge_expired(self, key):
        expires_at = self._expiry.get(key)
        if expires_at is not None and self._clock >= expires_at:
            self._data.pop(key, None)
            self._expiry.pop(key, None)

    async def set(self, key, value, ex=None, nx=False):
        self._purge_expired(key)
        if nx and key in self._data:
            return None
        self._data[key] = value
        if ex is not None:
            self._expiry[key] = self._clock + ex
        else:
            self._expiry.pop(key, None)
        return True

    async def get(self, key):
        self._purge_expired(key)
        return self._data.get(key)

    async def delete(self, key):
        self._data.pop(key, None)
        self._expiry.pop(key, None)


def _topics_payload():
    return [
        {"tag": "ai", "label": "AI", "count": 12, "last_active_at": "2026-08-26T00:00:00+00:00"},
        {"tag": "llm", "label": "LLM", "count": 5, "last_active_at": "2026-08-25T00:00:00+00:00"},
    ]


class TestPublishTopicStatsUpdate:
    """Contract: docs/dev-guide/design/tech-tab.md §3.8 topic_stats_update."""

    @patch("app.services.sse.event_router")
    async def test_publishes_topics_array_on_tech_channel(self, mock_router):
        """Payload is the full topics array (GET /tech/topics `data` shape) on channel tech."""
        mock_router.push_event = AsyncMock()
        fake_redis = _FakeRedis()
        payload = _topics_payload()

        with (
            patch("app.core.redis.get_redis_client", AsyncMock(return_value=fake_redis)),
            patch.object(SSEService, "_load_topic_stats", AsyncMock(return_value=payload)) as loader,
        ):
            service = SSEService()
            published = await service.publish_topic_stats_update("tenant-1")

        assert published is True
        loader.assert_awaited_once_with("tenant-1")
        mock_router.push_event.assert_awaited_once_with(
            category="tech",
            event_type=SSEEventType.TOPIC_STATS_UPDATE,
            data=payload,
            tenant_id="tenant-1",
        )

    @patch("app.services.sse.event_router")
    async def test_throttle_window_and_expiry(self, mock_router):
        """First trigger publishes; within the 900s window it skips; after expiry it publishes again."""
        mock_router.push_event = AsyncMock()
        fake_redis = _FakeRedis()

        with (
            patch("app.core.redis.get_redis_client", AsyncMock(return_value=fake_redis)),
            patch.object(SSEService, "_load_topic_stats", AsyncMock(return_value=_topics_payload())),
        ):
            service = SSEService()
            assert await service.publish_topic_stats_update("tenant-1") is True
            # Still inside the window → throttled.
            fake_redis.advance(RedisKeys.TOPIC_STATS_PUSHED_TTL - 1)
            assert await service.publish_topic_stats_update("tenant-1") is False
            # Window expired → next trigger publishes again.
            fake_redis.advance(2)
            assert await service.publish_topic_stats_update("tenant-1") is True

        assert mock_router.push_event.await_count == 2

    @patch("app.services.sse.event_router")
    async def test_throttle_is_per_tenant(self, mock_router):
        """The throttle key is namespaced per tenant, so tenants do not block each other."""
        mock_router.push_event = AsyncMock()
        fake_redis = _FakeRedis()

        with (
            patch("app.core.redis.get_redis_client", AsyncMock(return_value=fake_redis)),
            patch.object(SSEService, "_load_topic_stats", AsyncMock(return_value=_topics_payload())),
        ):
            service = SSEService()
            assert await service.publish_topic_stats_update("tenant-a") is True
            assert await service.publish_topic_stats_update("tenant-b") is True

        assert mock_router.push_event.await_count == 2
        assert RedisKeys.topic_stats_pushed_key("tenant-a") in fake_redis._data
        assert RedisKeys.topic_stats_pushed_key("tenant-b") in fake_redis._data

    @patch("app.services.sse.event_router")
    async def test_redis_unavailable_skips_silently(self, mock_router):
        """When Redis is down the push is skipped without raising and without publishing."""
        mock_router.push_event = AsyncMock()

        with patch("app.core.redis.get_redis_client", AsyncMock(side_effect=ConnectionError("redis down"))):
            service = SSEService()
            published = await service.publish_topic_stats_update("tenant-1")

        assert published is False
        mock_router.push_event.assert_not_awaited()

    @patch("app.services.sse.event_router")
    async def test_load_failure_releases_throttle(self, mock_router):
        """On stats load failure the throttle key is deleted so the next trigger retries."""
        mock_router.push_event = AsyncMock()
        fake_redis = _FakeRedis()
        throttle_key = RedisKeys.topic_stats_pushed_key("tenant-1")

        with (
            patch("app.core.redis.get_redis_client", AsyncMock(return_value=fake_redis)),
            patch("app.core.redis.redis_delete", AsyncMock()) as mock_delete,
            patch.object(SSEService, "_load_topic_stats", AsyncMock(side_effect=RuntimeError("db down"))),
        ):
            service = SSEService()
            published = await service.publish_topic_stats_update("tenant-1")

        assert published is False
        mock_delete.assert_awaited_once_with(throttle_key)
        mock_router.push_event.assert_not_awaited()
