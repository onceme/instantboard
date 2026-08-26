"""Integration tests for the topic_stats_update SSE push (tech-tab.md §3.8).

Exercises SSEService.publish_topic_stats_update through the real SSEEventRouter
with the module-wide mocked Redis (tests/integration/conftest.py): payload and
channel contract, the 900s SET NX throttle window (virtual clock), and the
per-channel history entry that reconnecting clients replay via Last-Event-ID.
Topic loading (TechService.get_topics) is stubbed — the DB path is already
covered by test_services_tech.py / test_api_tech.py.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.redis import RedisKeys
from app.core.sse_router import SSEEventType, event_router
from app.services.sse import SSEService

TOPICS = [
    {"tag": "ai", "label": "人工智能", "count": 42, "last_active_at": "2026-08-26T08:00:00+00:00"},
    {"tag": "llm", "label": "大语言模型", "count": 7, "last_active_at": "2026-08-26T07:30:00+00:00"},
]


@pytest.fixture(autouse=True)
def _clean_tech_channel(app_with_overrides):
    """The module-scoped MockRedis outlives every test: reset the tech channel
    history and any throttle keys so each case starts from a clean window."""
    _, mock_redis = app_with_overrides
    mock_redis._data.pop(RedisKeys.stream_history_key("tech"), None)
    for key in [k for k in mock_redis._data if k.startswith("tech:topic_stats_pushed:")]:
        mock_redis._data.pop(key, None)
        mock_redis._expiry.pop(key, None)
    yield


class TestPublishTopicStatsUpdate:
    async def test_publish_writes_topic_stats_event_to_tech_history(self, app_with_overrides):
        _, mock_redis = app_with_overrides
        tenant_id = str(uuid.uuid4())

        with patch.object(SSEService, "_load_topic_stats", AsyncMock(return_value=TOPICS)) as loader:
            published = await SSEService().publish_topic_stats_update(tenant_id)

        assert published is True
        loader.assert_awaited_once_with(tenant_id)

        history = mock_redis._data.get(RedisKeys.stream_history_key("tech"))
        assert history, "publish must persist the event to the tech history for Last-Event-ID replay"
        event = json.loads(history[0])
        assert event["event_type"] == SSEEventType.TOPIC_STATS_UPDATE == "topic_stats_update"
        assert event["channel"] == "tech"
        assert event["tenant_id"] == tenant_id
        # Payload is the bare topics array (GET /tech/topics `data` shape).
        assert event["data"] == TOPICS
        assert event["event_id"]
        assert event["published_at"]

    async def test_publish_delivers_to_tech_subscribers(self, app_with_overrides):
        _, _ = app_with_overrides
        tenant_id = str(uuid.uuid4())

        conn = event_router.register(f"{tenant_id}:user-1:{uuid.uuid4()}", ["tech"], tenant_id)
        try:
            with patch.object(SSEService, "_load_topic_stats", AsyncMock(return_value=TOPICS)):
                published = await SSEService().publish_topic_stats_update(tenant_id)

            assert published is True
            event = await conn.queue.get()
            assert event["event_type"] == "topic_stats_update"
            assert event["data"] == TOPICS
        finally:
            event_router.unregister(conn.client_id)

    async def test_throttle_window_skips_then_republishes_after_expiry(self, app_with_overrides):
        _, mock_redis = app_with_overrides
        tenant_id = str(uuid.uuid4())
        throttle_key = RedisKeys.topic_stats_pushed_key(tenant_id)

        with patch.object(SSEService, "_load_topic_stats", AsyncMock(return_value=TOPICS)) as loader:
            service = SSEService()
            # First trigger in a fresh window publishes and arms the 900s throttle.
            assert await service.publish_topic_stats_update(tenant_id) is True
            assert mock_redis._expiry.get(throttle_key) == RedisKeys.TOPIC_STATS_PUSHED_TTL

            # Inside the window: no stats load, no second push.
            assert await service.publish_topic_stats_update(tenant_id) is False
            mock_redis.advance(RedisKeys.TOPIC_STATS_PUSHED_TTL - 1)
            assert await service.publish_topic_stats_update(tenant_id) is False

            # Window expired: the next trigger publishes again.
            mock_redis.advance(2)
            assert await service.publish_topic_stats_update(tenant_id) is True

        assert loader.await_count == 2
        history = mock_redis._data.get(RedisKeys.stream_history_key("tech"))
        events = [json.loads(raw) for raw in history]
        assert [e["tenant_id"] for e in events] == [tenant_id, tenant_id]
