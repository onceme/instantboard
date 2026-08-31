"""E2E: fund intraday NAV pipeline (fund-intraday-nav.md, M2 phase C).

Minimal closed loop: seeded fund symbol -> add to watchlist (ingest hook
fires) -> fund-nav/batch returns a latest_official entry WITHOUT a
"fund symbol not found" error (the M2 fund-symbol-availability delta) ->
the finance SSE channel accepts a subscription that carries nav_batch_update.

The holdings ingest hook is patched to a no-op: it is fire-and-forget and
would otherwise perform a live f10 fetch from the e2e event loop. The SSE
asserted here is subscription reachability on the finance channel (the
compute cycle that pushes nav_batch_update only runs while the CN market is
open, so a live push round-trip is left to the integration layer).
"""

import asyncio
import contextlib
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from app.core.sse_router import SSEEventType, event_router
from app.models.finance import FinanceSymbol
from tests.conftest import test_session_factory
from tests.e2e.conftest import bearer, make_e2e_token

pytestmark = pytest.mark.e2e

FUND_CODE = "519999"  # leading-5 CN fund family, unique to this module


@pytest_asyncio.fixture
async def fund_nav_env(e2e_tenant_and_user):
    tenant_id = uuid.UUID(e2e_tenant_and_user["tenant_id"])
    async with test_session_factory() as session:
        session.add(
            FinanceSymbol(
                tenant_id=tenant_id,
                symbol=FUND_CODE,
                name="E2E 沪深测试基金",
                type="fund",
                market="CN",
                exchange="",
                currency="CNY",
                is_active=True,
            )
        )
        await session.commit()
    return e2e_tenant_and_user


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
            (b"user-agent", b"e2e-fund-nav/1.0"),
        ],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


class TestFundNavPipelineJourney:
    async def test_watchlist_add_fires_ingest_hook(self, aclient, fund_nav_env):
        headers = bearer(make_e2e_token(fund_nav_env["tenant_id"], fund_nav_env["user_id"]))

        with patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn:
            resp = await aclient.post("/api/v1/finance/watchlist", headers=headers, json={"symbol": FUND_CODE})
        assert resp.status_code == 201
        item = resp.json()["data"]
        assert item["symbol"] == FUND_CODE
        # holdings ingest hook fired with the normalized 6-digit code (§5.1)
        mock_spawn.assert_called_once_with(FUND_CODE)

        # Suffix variant resolves the same registered symbol (M2 phase A)
        with patch("app.services.fund_holdings.spawn_holdings_ingestion"):
            resp = await aclient.post("/api/v1/finance/watchlist", headers=headers, json={"symbol": f"{FUND_CODE}.SS"})
        assert resp.status_code == 409  # duplicate: resolved to the same symbol_id
        assert resp.json()["detail"]["error"]["code"] == "DUPLICATE_WATCHLIST_ITEM"

    async def test_batch_returns_official_entry_not_symbol_error(self, aclient, fund_nav_env):
        """The M2 delta vs. M1: a seeded fund code must NOT come back as
        'fund symbol not found' — without an anchor it degrades to a
        latest_official entry with nulls (frozen)."""
        headers = bearer(make_e2e_token(fund_nav_env["tenant_id"], fund_nav_env["user_id"]))

        with patch("app.services.fund_holdings.spawn_holdings_ingestion"):
            resp = await aclient.get("/api/v1/finance/fund-nav/batch", params={"symbols": FUND_CODE}, headers=headers)
        assert resp.status_code == 200
        entry = resp.json()["data"][0]
        assert entry["symbol"] == FUND_CODE
        assert entry["estimate_method"] == "latest_official"
        assert entry["quote_status"] == "frozen"
        assert entry.get("error") is None  # the M1 failure mode must be gone
        assert entry["nav_official"] is None  # no anchor seeded → nulls, not error

        # unknown code still degrades to an error entry, never a 404
        resp = await aclient.get("/api/v1/finance/fund-nav/batch", params={"symbols": "ZZZZZZ"}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"][0]["error"] == "unrecognized fund code"

    async def test_finance_sse_subscription_reachable_for_nav_batch(self, e2e_env, fund_nav_env, monkeypatch):
        """The finance channel accepts a subscription on which nav_batch_update
        is delivered (event type registered + handshake completes)."""
        from app.config import settings

        assert SSEEventType.NAV_BATCH_UPDATE == "nav_batch_update"

        app, _redis = e2e_env
        monkeypatch.setattr(settings, "sse_heartbeat_interval", 1)
        token = make_e2e_token(fund_nav_env["tenant_id"], fund_nav_env["user_id"])

        status: dict = {}
        chunks: asyncio.Queue = asyncio.Queue()
        events: list[str] = []
        connected_client_id: str | None = None

        async def receive():
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
                        if "connected" in events:
                            return

            await asyncio.wait_for(run_handshake(), timeout=15)
        finally:
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
