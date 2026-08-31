"""Unit tests for the intraday NAV push path (fund-intraday-nav.md §8).

Covers the 1bp change-detection signature, unchanged-array push suppression,
per-tenant payload filtering (cross-tenant isolation), the online-tenant
fan-out (system tenant always included), history=False delivery, and the
fund_nav_rt realtime cache writes (TTL 12s).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import SYSTEM_TENANT_ID
from app.core.sse_router import SSEEventType
from app.services import fund_intraday
from app.services.fund_intraday import FundIntradayService


@pytest.fixture(autouse=True)
def _clean_cycle_state():
    fund_intraday.reset_cycle_state()
    yield
    fund_intraday.reset_cycle_state()


def _svc():
    return FundIntradayService(db=AsyncMock(), governor=AsyncMock())


def _entry(symbol: str, change=0.5, method="holdings_weighted", status="mixed", nav=4.52) -> dict:
    return {
        "symbol": symbol,
        "name": f"fund-{symbol}",
        "nav_official": 4.5,
        "nav_official_date": "2026-08-28",
        "nav_estimate": nav,
        "estimate_change_percent": change,
        "estimate_method": method,
        "coverage_percent": 70.0,
        "holdings_report_date": "2026-06-30",
        "quote_status": status,
        "delayed_markets": ["HK"] if status != "realtime" else [],
        "holdings_stale": False,
        "estimate_timestamp": "2026-08-31T04:00:00+00:00",
    }


class TestResultSignature:
    def test_change_quantized_to_1bp(self):
        svc = _svc()
        sig_a = svc._result_signature(_entry("510300", change=0.8549))
        sig_b = svc._result_signature(_entry("510300", change=0.8551))
        # 0.8549% → 85 ×1bp, 0.8551% → 86 ×1bp
        assert sig_a[2] == 85
        assert sig_b[2] == 86
        assert sig_a != sig_b

    def test_sub_1bp_move_is_unchanged(self):
        svc = _svc()
        sig_a = svc._result_signature(_entry("510300", change=0.8501))
        sig_b = svc._result_signature(_entry("510300", change=0.8549))
        assert sig_a == sig_b  # both round to 85

    def test_method_and_status_participate(self):
        svc = _svc()
        base = _entry("510300")
        assert svc._result_signature(base) != svc._result_signature(_entry("510300", method="index_tracking"))
        assert svc._result_signature(base) != svc._result_signature(_entry("510300", status="realtime"))

    def test_none_change_signature(self):
        svc = _svc()
        sig = svc._result_signature(_entry("510300", change=None, method="latest_official", status="frozen", nav=None))
        assert sig[2] is None


class TestPayloadFor:
    RESULTS = [
        _entry("510300"),
        _entry("005827"),
        _entry("110011"),
    ]
    PER_TENANT = {
        "tenant-A": {"510300", "110011"},
        "tenant-B": {"005827"},
    }

    def test_tenant_sees_only_its_codes(self):
        svc = _svc()
        payload = svc.payload_for("tenant-A", self.RESULTS, self.PER_TENANT)
        assert [p["symbol"] for p in payload] == ["110011", "510300"]  # sorted

    def test_other_tenant_never_sees_foreign_codes(self):
        svc = _svc()
        payload = svc.payload_for("tenant-B", self.RESULTS, self.PER_TENANT)
        assert [p["symbol"] for p in payload] == ["005827"]

    def test_unknown_tenant_gets_nothing(self):
        svc = _svc()
        assert svc.payload_for("tenant-C", self.RESULTS, self.PER_TENANT) == []

    def test_system_tenant_gets_full_array(self):
        svc = _svc()
        payload = svc.payload_for(str(SYSTEM_TENANT_ID), self.RESULTS, self.PER_TENANT)
        assert len(payload) == 3


class TestPushBatch:
    async def _push(self, svc, results, per_tenant, online):
        client = AsyncMock()
        client.smembers = AsyncMock(return_value=online)
        with (
            patch("app.services.fund_intraday.get_redis_client", new_callable=AsyncMock, return_value=client),
            patch("app.services.fund_intraday.event_router") as mock_router,
        ):
            mock_router.push_event = AsyncMock()
            pushed = await svc._push_batch(results, per_tenant)
            return pushed, mock_router

    async def test_pushes_per_online_tenant_with_history_false(self):
        svc = _svc()
        results = [_entry("510300"), _entry("005827")]
        pushed, router = await self._push(svc, results, {"tenant-A": {"510300"}}, {"tenant-A", "tenant-B"})
        # tenant-A has its code; tenant-B's filter is empty → skipped; system
        # tenant is always included with the full array.
        assert pushed == 2
        calls = router.push_event.await_args_list
        tenants = [c.args[3] for c in calls]
        assert "tenant-A" in tenants
        assert str(SYSTEM_TENANT_ID) in tenants
        assert "tenant-B" not in tenants
        for call in calls:
            assert call.args[0] == "finance"
            assert call.args[1] == SSEEventType.NAV_BATCH_UPDATE
            assert call.kwargs.get("history") is False  # never fills the replay window

    async def test_unchanged_array_suppresses_second_push(self):
        svc = _svc()
        results = [_entry("510300")]
        pushed1, router1 = await self._push(svc, results, {"tenant-A": {"510300"}}, {"tenant-A"})
        assert pushed1 >= 1
        assert router1.push_event.await_count > 0
        # Identical payload a cycle later → change detection skips the push.
        pushed2, router2 = await self._push(svc, results, {"tenant-A": {"510300"}}, {"tenant-A"})
        assert pushed2 == 0
        router2.push_event.assert_not_awaited()

    async def test_1bp_move_within_threshold_still_suppressed(self):
        svc = _svc()
        pushed1, _ = await self._push(svc, [_entry("510300", change=0.8501)], {"tenant-A": {"510300"}}, {"tenant-A"})
        assert pushed1 >= 1
        pushed2, router2 = await self._push(
            svc, [_entry("510300", change=0.8549)], {"tenant-A": {"510300"}}, {"tenant-A"}
        )
        assert pushed2 == 0
        router2.push_event.assert_not_awaited()

    async def test_status_switch_forces_push(self):
        svc = _svc()
        await self._push(svc, [_entry("510300", status="mixed")], {"tenant-A": {"510300"}}, {"tenant-A"})
        pushed2, router2 = await self._push(
            svc, [_entry("510300", status="realtime")], {"tenant-A": {"510300"}}, {"tenant-A"}
        )
        assert pushed2 >= 1
        router2.push_event.assert_awaited()

    async def test_empty_online_set_still_pushes_system_tenant(self):
        svc = _svc()
        pushed, router = await self._push(svc, [_entry("510300")], {"tenant-A": {"510300"}}, set())
        assert pushed == 1
        assert router.push_event.await_args.args[3] == str(SYSTEM_TENANT_ID)

    async def test_redis_failure_skips_push_but_no_crash(self):
        svc = _svc()
        with (
            patch(
                "app.services.fund_intraday.get_redis_client",
                new_callable=AsyncMock,
                side_effect=RuntimeError("redis down"),
            ),
            patch("app.services.fund_intraday.event_router") as router,
        ):
            router.push_event = AsyncMock()
            pushed = await svc._push_batch([_entry("510300")], {"tenant-A": {"510300"}})
        assert pushed == 0
        router.push_event.assert_not_awaited()
        # signatures NOT updated → the next cycle retries the push
        assert fund_intraday._LAST_PUSH_SIGNATURES == {}


class TestRtCacheWrite:
    async def test_writes_each_result_with_ttl_12s(self):
        import json as _json

        svc = _svc()
        # pipeline.set() is a sync queueing call on the real client; only
        # execute() is awaited.
        pipe = MagicMock()
        pipe.set = MagicMock()
        pipe.execute = AsyncMock()
        client = AsyncMock()
        client.pipeline = MagicMock(return_value=pipe)
        results = [_entry("510300"), _entry("005827")]
        with patch("app.services.fund_intraday.get_redis_client", new_callable=AsyncMock, return_value=client):
            await svc._write_rt_cache(results)

        assert pipe.set.call_count == 2
        keys = [c.args[0] for c in pipe.set.call_args_list]
        assert keys == ["fund_nav_rt:510300", "fund_nav_rt:005827"]
        assert all(c.kwargs.get("ex") == 12 for c in pipe.set.call_args_list)
        stored = _json.loads(pipe.set.call_args_list[0].args[1])
        assert stored["symbol"] == "510300"
        pipe.execute.assert_awaited_once()

    async def test_empty_results_skip_pipeline(self):
        svc = _svc()
        client = AsyncMock()
        client.pipeline = MagicMock()
        with patch("app.services.fund_intraday.get_redis_client", new_callable=AsyncMock, return_value=client):
            await svc._write_rt_cache([])
        client.pipeline.assert_not_called()

    async def test_redis_failure_swallowed(self):
        svc = _svc()
        with patch(
            "app.services.fund_intraday.get_redis_client",
            new_callable=AsyncMock,
            side_effect=RuntimeError("down"),
        ):
            # must not raise — the compute cycle continues without the cache
            await svc._write_rt_cache([_entry("510300")])


class TestWriteCloseSnapshots:
    """Close-of-market forced snapshot (fund-intraday-nav.md §3.4 case 3)."""

    async def test_empty_context_writes_nothing(self):
        session = AsyncMock()
        session.commit = AsyncMock()
        with patch(
            "app.services.fund_intraday.FundIntradayService._upsert_flush_row",
            new_callable=AsyncMock,
        ) as mock_upsert:
            written = await fund_intraday.write_close_snapshots(session)
        assert written == 0
        mock_upsert.assert_not_awaited()
        session.commit.assert_not_awaited()

    async def test_writes_estimated_rows_and_skips_latest_official(self):
        hw = _entry("510300", method="holdings_weighted", status="realtime")
        it = _entry("005827", method="index_tracking", status="realtime")
        lo = _entry("110011", method="latest_official", status="frozen")
        fund_intraday._LAST_CYCLE_CONTEXT.update(
            {
                "results_by_code": {"510300": hw, "005827": it, "110011": lo},
                "ids_per_code": {"510300": ["s1"], "005827": ["s2"], "110011": ["s3"]},
            }
        )
        session = AsyncMock()
        session.commit = AsyncMock()
        redis_client = AsyncMock()
        redis_client.delete = AsyncMock(return_value=1)
        with (
            patch(
                "app.services.fund_intraday.FundIntradayService._upsert_flush_row",
                new_callable=AsyncMock,
                return_value=True,
            ) as mock_upsert,
            patch(
                "app.services.fund_intraday.get_redis_client",
                new_callable=AsyncMock,
                return_value=redis_client,
            ),
        ):
            written = await fund_intraday.write_close_snapshots(session)
        assert written == 2  # holdings_weighted + index_tracking, not latest_official
        assert mock_upsert.await_count == 2
        session.commit.assert_awaited_once()
        # throttle windows cleared for the next session
        assert redis_client.delete.await_count == 2
        # context + flush modes reset after the edge
        assert fund_intraday._LAST_CYCLE_CONTEXT == {}
        assert fund_intraday._LAST_FLUSH_MODES == {}

    async def test_single_fund_upsert_failure_does_not_block_rest(self):
        hw = _entry("510300", method="holdings_weighted", status="realtime")
        it = _entry("005827", method="index_tracking", status="realtime")
        fund_intraday._LAST_CYCLE_CONTEXT.update(
            {
                "results_by_code": {"510300": hw, "005827": it},
                "ids_per_code": {"510300": ["s1"], "005827": ["s2"]},
            }
        )
        session = AsyncMock()
        session.commit = AsyncMock()
        with (
            patch(
                "app.services.fund_intraday.FundIntradayService._upsert_flush_row",
                new_callable=AsyncMock,
                side_effect=[RuntimeError("db glitch"), True],
            ),
            patch(
                "app.services.fund_intraday.get_redis_client",
                new_callable=AsyncMock,
                return_value=AsyncMock(delete=AsyncMock(return_value=1)),
            ),
        ):
            written = await fund_intraday.write_close_snapshots(session)
        assert written == 1  # second fund still written
        session.commit.assert_awaited_once()

    async def test_commit_failure_returns_zero_without_raising(self):
        hw = _entry("510300", method="holdings_weighted", status="realtime")
        fund_intraday._LAST_CYCLE_CONTEXT.update(
            {"results_by_code": {"510300": hw}, "ids_per_code": {"510300": ["s1"]}}
        )
        session = AsyncMock()
        session.commit = AsyncMock(side_effect=RuntimeError("commit failed"))
        session.rollback = AsyncMock()
        with (
            patch(
                "app.services.fund_intraday.FundIntradayService._upsert_flush_row",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "app.services.fund_intraday.get_redis_client",
                new_callable=AsyncMock,
                return_value=AsyncMock(delete=AsyncMock(return_value=1)),
            ),
        ):
            written = await fund_intraday.write_close_snapshots(session)
        assert written == 0
        session.rollback.assert_awaited_once()
