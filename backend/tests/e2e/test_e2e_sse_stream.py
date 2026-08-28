"""E2E: SSE stream handshake.

Connect to /stream/{category} with a JWT -> assert text/event-stream, the initial
`connected` event, then a heartbeat within the (shortened) interval. httpx's
ASGITransport buffers whole responses, so the stream is driven through the ASGI
interface directly. Terminating the stream by making the connection queue raise
KeyError mirrors the generator's own exit contract (see app/api/v1/sse.py), which
keeps the disconnect audit clean instead of cancelling mid-flight. Guarded by a
hard timeout so the suite can never hang on the open stream.
"""

import asyncio
import contextlib
import json

import pytest

from app.config import settings
from app.core.sse_router import event_router
from tests.e2e.conftest import make_e2e_token

pytestmark = pytest.mark.e2e


class _ClosingQueue:
    async def get(self):
        raise KeyError("e2e-close-stream")


def _build_scope(path: str, token: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": f"token={token}".encode("ascii"),
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"accept", b"text/event-stream"),
            (b"user-agent", b"e2e-sse-handshake/1.0"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


class TestSSEHandshake:
    async def test_stream_requires_valid_token(self, aclient):
        resp = await aclient.get("/api/v1/stream/finance")
        assert resp.status_code == 422

        resp = await aclient.get("/api/v1/stream/finance", params={"token": "invalid-jwt"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"

    async def test_stream_handshake_connected_then_heartbeat(self, e2e_env, e2e_tenant_and_user, monkeypatch):
        app, _ = e2e_env
        # Shorten the idle timeout that drives heartbeat frames so the test stays fast.
        monkeypatch.setattr(settings, "sse_heartbeat_interval", 1)
        token = make_e2e_token(e2e_tenant_and_user["tenant_id"], e2e_tenant_and_user["user_id"])

        status = {}
        chunks: asyncio.Queue = asyncio.Queue()
        events: list[str] = []
        connected_client_id: str | None = None

        async def receive():
            # Keep the connection open; the stream exits via _ClosingQueue below.
            await asyncio.Event().wait()

        async def send(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                status["headers"] = dict(message.get("headers", []))
            elif message["type"] == "http.response.body":
                await chunks.put(message.get("body", b""))

        app_task = asyncio.create_task(app(_build_scope("/api/v1/stream/finance", token), receive, send))

        try:

            async def run_handshake():
                nonlocal connected_client_id
                buffer = ""

                # Drain chunks until the connected + heartbeat frames have arrived.
                while True:
                    buffer += (await asyncio.wait_for(chunks.get(), timeout=10)).decode("utf-8")
                    while "\n\n" in buffer:
                        frame, buffer = buffer.split("\n\n", 1)
                        event_name = None
                        data_payload = None
                        for line in frame.split("\n"):
                            if line.startswith("event:"):
                                event_name = line.split(":", 1)[1].strip()
                            elif line.startswith("data:"):
                                data_payload = line.split(":", 1)[1].strip()
                        if event_name is None:
                            continue
                        events.append(event_name)
                        if event_name == "connected" and data_payload:
                            connected_client_id = json.loads(data_payload).get("client_id")
                        if {"connected", "heartbeat"} <= set(events):
                            return

            await asyncio.wait_for(run_handshake(), timeout=15)
        finally:
            # Close the stream through its own exit contract (queue.get() -> KeyError ->
            # break -> disconnect audit), then let the ASGI task finish cleanly.
            conn = event_router.get_connection(connected_client_id) if connected_client_id else None
            if conn is not None:
                conn.queue = _ClosingQueue()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(app_task, timeout=10)
            if not app_task.done():
                app_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await app_task

        assert status["code"] == 200
        assert status["headers"].get(b"content-type", b"").startswith(b"text/event-stream")
        assert events[0] == "connected"
        assert "heartbeat" in events

        # Disconnect audit: the sse_connections row written on connect is closed.
        from sqlalchemy import select

        from app.models.sse import SSEConnection as SSEConnectionModel
        from tests.conftest import test_session_factory

        async with test_session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(SSEConnectionModel).where(SSEConnectionModel.user_id == e2e_tenant_and_user["user_id"])
                    )
                )
                .scalars()
                .all()
            )
        assert rows, "connect() must audit the SSE connection row"
        assert all(row.disconnected_at is not None for row in rows)
