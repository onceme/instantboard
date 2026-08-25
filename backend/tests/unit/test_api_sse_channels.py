"""Unit tests for the /stream/all?channels= multi-channel selector (api.md §3.8)."""

from unittest.mock import AsyncMock, patch

import pytest

from app.api.v1.sse import VALID_CHANNELS, _parse_channels, collect_replay_events
from app.core.exceptions import ValidationError
from app.core.sse_router import SSEEventType


def _event(event_id: str, published_at: str) -> dict:
    return {
        "event_type": SSEEventType.ITEM_UPDATE.value,
        "channel": "finance",
        "data": {"id": event_id},
        "tenant_id": "tenant1",
        "event_id": event_id,
        "published_at": published_at,
    }


class TestParseChannels:
    def test_single_channel(self):
        assert _parse_channels("finance") == ["finance"]

    def test_multiple_channels_sorted(self):
        assert _parse_channels("tech,finance") == ["finance", "tech"]

    def test_strips_whitespace_around_items(self):
        assert _parse_channels(" finance , tech ") == ["finance", "tech"]

    def test_drops_empty_segments(self):
        assert _parse_channels("finance,,tech,") == ["finance", "tech"]

    def test_deduplicates_and_sorts(self):
        assert _parse_channels("tech,finance,tech") == ["finance", "tech"]

    def test_all_valid_channels_accepted(self):
        assert _parse_channels(",".join(sorted(VALID_CHANNELS))) == sorted(VALID_CHANNELS)

    @pytest.mark.parametrize("raw", ["", ",", " , ,, "])
    def test_empty_result_raises_validation_error(self, raw):
        with pytest.raises(ValidationError) as exc_info:
            _parse_channels(raw)
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert exc_info.value.error_details == [{"field": "channels", "message": "must list at least one channel"}]

    def test_unknown_channel_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            _parse_channels("finance,bogus")
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert exc_info.value.error_details == [{"field": "channels", "message": "unknown channel: bogus"}]

    def test_multiple_unknown_channels_all_reported_sorted(self):
        with pytest.raises(ValidationError) as exc_info:
            _parse_channels("nope,also-nope")
        assert exc_info.value.error_details == [
            {"field": "channels", "message": "unknown channel: also-nope"},
            {"field": "channels", "message": "unknown channel: nope"},
        ]


class TestReplayChannelsSubset:
    async def test_all_stream_with_subset_replays_only_selected_channels(self):
        async def per_channel(channel, last_event_id, tenant_id):
            return {
                "finance": [_event("f1", "2026-08-25T10:00:02+00:00")],
                "tech": [_event("t1", "2026-08-25T10:00:01+00:00")],
            }.get(channel, [])

        with patch("app.api.v1.sse.event_router.get_missed_events", side_effect=per_channel) as mock_get:
            result = await collect_replay_events("all", "anchor", "tenant1", ["finance", "tech"])

        queried = {call.args[0] for call in mock_get.await_args_list}
        assert queried == {"finance", "tech"}
        assert [e["event_id"] for e in result] == ["t1", "f1"]

    async def test_all_stream_without_channels_defaults_to_all(self):
        with patch("app.api.v1.sse.event_router.get_missed_events", AsyncMock(return_value=[])) as mock_get:
            await collect_replay_events("all", "anchor", "tenant1")

        queried = {call.args[0] for call in mock_get.await_args_list}
        assert queried == set(VALID_CHANNELS)

    async def test_single_category_ignores_channels_argument(self):
        with patch("app.api.v1.sse.event_router.get_missed_events", AsyncMock(return_value=[])) as mock_get:
            await collect_replay_events("finance", "e1", "tenant1", ["finance", "tech"])

        mock_get.assert_awaited_once_with("finance", "e1", "tenant1")
