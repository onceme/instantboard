"""SSE Last-Event-ID replay: history buffer, anchor resolution, tenant filter, all-channel merge."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.api.v1.sse import ALL_CHANNELS, collect_replay_events
from app.core.redis import RedisKeys, redis_lrange, redis_push_history
from app.core.sse_router import SSEEventRouter, SSEEventType


def _message(event_id: str, tenant_id: str | None = "tenant1", published_at: str = "2026-08-25T10:00:00+00:00") -> dict:
    return {
        "event_type": SSEEventType.ITEM_UPDATE.value,
        "channel": "finance",
        "data": {"id": event_id},
        "tenant_id": tenant_id,
        "event_id": event_id,
        "published_at": published_at,
    }


class TestStreamHistoryKeys:
    def test_stream_history_key(self):
        assert RedisKeys.stream_history_key("finance") == "stream:history:finance"
        assert RedisKeys.stream_history_key("tech") == "stream:history:tech"

    def test_stream_history_constants(self):
        assert RedisKeys.STREAM_HISTORY == "stream:history:{category}"
        assert RedisKeys.STREAM_HISTORY_LIMIT == 500
        assert RedisKeys.STREAM_HISTORY_TTL == 1800


class TestRedisPushHistory:
    async def test_pipeline_lpush_ltrim_expire(self):
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[1, True, True])
        client = MagicMock()
        client.pipeline.return_value = pipe

        with patch("app.core.redis.get_redis_client", AsyncMock(return_value=client)):
            await redis_push_history("stream:history:finance", _message("e1"), 500, 1800)

        client.pipeline.assert_called_once()
        lpush_args = pipe.lpush.call_args[0]
        assert lpush_args[0] == "stream:history:finance"
        assert json.loads(lpush_args[1]) == _message("e1")
        pipe.ltrim.assert_called_once_with("stream:history:finance", 0, 499)
        pipe.expire.assert_called_once_with("stream:history:finance", 1800)
        pipe.execute.assert_awaited_once()

    async def test_no_expire_without_ttl(self):
        pipe = MagicMock()
        pipe.execute = AsyncMock(return_value=[1, True])
        client = MagicMock()
        client.pipeline.return_value = pipe

        with patch("app.core.redis.get_redis_client", AsyncMock(return_value=client)):
            await redis_push_history("stream:history:tech", {"a": 1}, 10)

        pipe.ltrim.assert_called_once_with("stream:history:tech", 0, 9)
        pipe.expire.assert_not_called()

    async def test_failure_logged_not_raised(self):
        client = MagicMock()
        client.pipeline.side_effect = Exception("connection refused")

        with (
            patch("app.core.redis.get_redis_client", AsyncMock(return_value=client)),
            patch("app.core.redis.logger") as mock_logger,
        ):
            await redis_push_history("stream:history:finance", _message("e1"), 500, 1800)
            mock_logger.warning.assert_called_once()


class TestRedisLrange:
    async def test_returns_slice(self):
        client = AsyncMock()
        client.lrange.return_value = ["newest", "older"]
        with patch("app.core.redis.get_redis_client", AsyncMock(return_value=client)):
            result = await redis_lrange("stream:history:finance")
            assert result == ["newest", "older"]
            client.lrange.assert_awaited_once_with("stream:history:finance", 0, -1)

    async def test_failure_returns_empty_list(self):
        client = AsyncMock()
        client.lrange.side_effect = Exception("connection refused")
        with (
            patch("app.core.redis.get_redis_client", AsyncMock(return_value=client)),
            patch("app.core.redis.logger") as mock_logger,
        ):
            result = await redis_lrange("stream:history:finance")
            assert result == []
            mock_logger.warning.assert_called_once()


class TestPushEventHistoryWrite:
    def setup_method(self):
        self.router = SSEEventRouter()
        self.router._connections = {}
        self.router._subscriptions = {}

    async def test_push_event_writes_history_before_publish(self):
        self.router.register("client1", ["finance"], "tenant1")
        call_order = []

        async def track_history(*args, **kwargs):
            call_order.append("history")

        async def track_publish(*args, **kwargs):
            call_order.append("publish")

        with (
            patch("app.core.sse_router.redis_push_history", side_effect=track_history) as mock_history,
            patch("app.core.sse_router.redis_publish", side_effect=track_publish),
        ):
            await self.router.push_event("finance", SSEEventType.ITEM_UPDATE, {"msg": "test"}, "tenant1")

        assert call_order == ["history", "publish"]
        history_key, message, max_len, ttl = mock_history.call_args[0]
        assert history_key == "stream:history:finance"
        assert max_len == RedisKeys.STREAM_HISTORY_LIMIT
        assert ttl == RedisKeys.STREAM_HISTORY_TTL
        assert "event_id" in message
        assert message["data"] == {"msg": "test"}

    async def test_fallback_path_skips_history_write(self):
        """Publish failure falls into the degraded (direct-push) branch, which
        performs no history write: the single pre-publish attempt is the only one."""
        conn = self.router.register("client1", ["finance"], "tenant1")

        with (
            patch("app.core.sse_router.redis_push_history", new_callable=AsyncMock) as mock_history,
            patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish,
        ):
            mock_publish.side_effect = Exception("Redis down")
            await self.router.push_event("finance", SSEEventType.ITEM_UPDATE, {"msg": "test"}, "tenant1")

        assert mock_history.await_count == 1
        # Event still delivered via the in-process fallback.
        assert conn.events_sent_count == 1

    async def test_history_write_failure_degrades_to_direct_push(self):
        conn = self.router.register("client1", ["finance"], "tenant1")

        with (
            patch("app.core.sse_router.redis_push_history", new_callable=AsyncMock) as mock_history,
            patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish,
            patch("app.core.sse_router.logger") as mock_logger,
        ):
            mock_history.side_effect = Exception("history store down")
            await self.router.push_event("finance", SSEEventType.ITEM_UPDATE, {"msg": "test"}, "tenant1")

        mock_publish.assert_not_awaited()
        mock_logger.warning.assert_called()
        assert conn.events_sent_count == 1


class TestGetMissedEvents:
    def setup_method(self):
        self.router = SSEEventRouter()

    def _stored(self, *events: dict) -> list[str]:
        """Return events as the history list sees them: newest first."""
        return [json.dumps(e) for e in reversed(events)]

    async def test_returns_events_after_anchor_in_order(self):
        e1, e2, e3 = (_message(f"e{i}", published_at=f"2026-08-25T10:00:0{i}+00:00") for i in range(1, 4))

        with patch("app.core.sse_router.redis_lrange", AsyncMock(return_value=self._stored(e1, e2, e3))):
            missed = await self.router.get_missed_events("finance", "e1", "tenant1")

        assert [e["event_id"] for e in missed] == ["e2", "e3"]

    async def test_anchor_is_latest_returns_empty(self):
        e1, e2 = _message("e1"), _message("e2")

        with patch("app.core.sse_router.redis_lrange", AsyncMock(return_value=self._stored(e1, e2))):
            missed = await self.router.get_missed_events("finance", "e2", "tenant1")

        assert missed == []

    async def test_unknown_anchor_returns_empty(self):
        with patch("app.core.sse_router.redis_lrange", AsyncMock(return_value=self._stored(_message("e1")))):
            missed = await self.router.get_missed_events("finance", "unknown-id", "tenant1")

        assert missed == []

    async def test_empty_history_returns_empty(self):
        with patch("app.core.sse_router.redis_lrange", AsyncMock(return_value=[])):
            missed = await self.router.get_missed_events("finance", "e1", "tenant1")

        assert missed == []

    async def test_redis_failure_returns_empty(self):
        with (
            patch("app.core.sse_router.redis_lrange", AsyncMock(side_effect=Exception("Redis down"))),
            patch("app.core.sse_router.logger") as mock_logger,
        ):
            missed = await self.router.get_missed_events("finance", "e1", "tenant1")

        assert missed == []
        mock_logger.debug.assert_called()

    async def test_tenant_filtering(self):
        own = _message("e2", tenant_id="tenant1")
        other = _message("e3", tenant_id="tenant2")
        global_event = _message("e4", tenant_id=None)
        anchor = _message("e1", tenant_id="tenant2")

        with patch(
            "app.core.sse_router.redis_lrange", AsyncMock(return_value=self._stored(anchor, own, other, global_event))
        ):
            missed = await self.router.get_missed_events("finance", "e1", "tenant1")

        # Other tenants' events are dropped; tenant-less events pass through.
        assert [e["event_id"] for e in missed] == ["e2", "e4"]

    async def test_invalid_json_entries_skipped(self):
        e1, e2 = _message("e1"), _message("e2")

        with patch(
            "app.core.sse_router.redis_lrange",
            AsyncMock(return_value=[json.dumps(e2), "corrupt{{{", json.dumps(e1)]),
        ):
            missed = await self.router.get_missed_events("finance", "e1", "tenant1")

        assert [e["event_id"] for e in missed] == ["e2"]


class TestCollectReplayEvents:
    async def test_single_channel_passthrough(self):
        events = [_message("e2"), _message("e3")]

        with patch("app.api.v1.sse.event_router.get_missed_events", AsyncMock(return_value=events)) as mock_get:
            result = await collect_replay_events("finance", "e1", "tenant1")

        mock_get.assert_awaited_once_with("finance", "e1", "tenant1")
        assert result == events

    async def test_all_channel_merges_in_published_at_order(self):
        finance_events = [
            _message("f1", published_at="2026-08-25T10:00:01+00:00"),
            _message("f2", published_at="2026-08-25T10:00:04+00:00"),
        ]
        tech_events = [_message("t1", published_at="2026-08-25T10:00:02+00:00")]
        dashboard_events = [_message("d1", published_at="2026-08-25T10:00:03+00:00")]

        async def per_channel(category, last_event_id, tenant_id):
            return {
                "finance": finance_events,
                "tech": tech_events,
                "dashboard": dashboard_events,
                "admin": [],
            }.get(category, [])

        with patch("app.api.v1.sse.event_router.get_missed_events", side_effect=per_channel) as mock_get:
            result = await collect_replay_events("all", "anchor", "tenant1")

        assert mock_get.await_count == len(ALL_CHANNELS)
        queried = {call.args[0] for call in mock_get.await_args_list}
        assert queried == set(ALL_CHANNELS)
        assert [e["event_id"] for e in result] == ["f1", "t1", "d1", "f2"]

    async def test_all_channel_empty_histories(self):
        with patch("app.api.v1.sse.event_router.get_missed_events", AsyncMock(return_value=[])):
            result = await collect_replay_events("all", "anchor", "tenant1")

        assert result == []
