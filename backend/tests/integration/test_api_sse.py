"""Tests for /api/v1/stream (SSE) endpoints."""

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import StreamingResponse

from app.core.security import create_access_token


def _token(role="admin", tenant_id=None):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "role": role,
            "provider": "github",
            "type": "access",
        }
    )


class TestSSEStream:
    def test_stream_requires_token(self, client):
        resp = client.get("/api/v1/stream/finance")
        assert resp.status_code == 422

    def test_stream_invalid_token(self, client):
        resp = client.get("/api/v1/stream/finance?token=invalid-jwt")
        assert resp.status_code == 401

    @patch("app.api.v1.sse.sse_service")
    @patch("app.api.v1.sse.event_router")
    def test_stream_returns_event_stream(self, mock_router, mock_svc, client):
        """Verify that the stream endpoint returns a StreamingResponse with correct headers.

        We do NOT consume the body (which would block indefinitely on a heartbeat loop).
        Instead, we make the queue raise KeyError on first get() to terminate the stream
        after the connected event.
        """
        token = _token()

        mock_conn = MagicMock()

        # Make queue.get() raise KeyError immediately to stop the stream loop
        async def mock_get_that_breaks():
            raise KeyError("stop")

        mock_conn.queue = MagicMock()
        mock_conn.queue.get = mock_get_that_breaks
        mock_conn.events_sent_count = 0
        mock_router.get_connection.return_value = mock_conn

        mock_svc.connect = AsyncMock()
        mock_svc.disconnect = AsyncMock()

        resp = client.get(
            f"/api/v1/stream/finance?token={token}",
            headers={"Accept": "text/event-stream"},
        )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")


class TestSSEStatus:
    def test_status_no_token_returns_empty(self, client):
        resp = client.get("/api/v1/stream/status")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["active_channels"] == []
        assert data["connection_id"] is None

    @patch("app.api.v1.sse.event_router")
    def test_status_with_token(self, mock_router, client):
        token = _token()
        mock_router._connections = {}

        resp = client.get(f"/api/v1/stream/status?token={token}")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert isinstance(data["active_channels"], list)


class TestSSEStats:
    @patch("app.api.v1.sse.sse_service")
    def test_stats_returns_data(self, mock_svc, client):
        async def mock_get_stats():
            return {
                "total_connections": 5,
                "connections_by_channel": {"finance": 3, "tech": 2},
                "peak_connections_24h": 10,
                "total_events_pushed": 500,
            }

        mock_svc.get_sse_stats = AsyncMock(side_effect=mock_get_stats)

        resp = client.get("/api/v1/stream/stats")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_connections" in data
