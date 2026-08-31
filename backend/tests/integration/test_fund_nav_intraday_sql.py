"""Real-SQL integration tests for the fund intraday NAV pipeline (M2 phase C).

Follows the dual-backend pattern of test_watchlist_cache_sql.py: real DB via
db_session with dict-backed fake Redis patched over the module-level helpers.
Coverage (fund-intraday-nav.md):

- compute cycle end-to-end: followed union → holdings → tiered estimate →
  tenant-filtered push (cross-tenant isolation, system tenant full array)
- empty union → zero upstream work
- downsampled DB flush: throttle window, expiry release, state-switch force
- REST batch matrix: unknown code entry, >50 → 400, rt-cache hit, miss →
  latest_official + lazy holdings-ingest hook
- search fund registration + variant spelling resolution

The close-of-market gate-edge snapshot stays at the unit layer
(test_fund_intraday_push.py::TestWriteCloseSnapshots): the integration setup
cannot advance the market gate without duplicating scheduler internals.
Mutation tests are PG-only (str ids bound against UUID columns, same gate as
test_watchlist_cache_sql.py).
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys
from app.models.finance import (
    FinanceSymbol,
    FundHoldingsMeta,
    FundHoldingSnapshot,
    FundNAVEstimate,
)
from app.models.tenant import Tenant
from app.models.user import User
from app.models.watchlist import WatchlistItem
from app.services import fund_intraday
from app.services.fund_intraday import FundIntradayService
from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import MockRedis, make_auth_header

REPORT_DATE = date.today() - timedelta(days=10)
ANCHOR_DATE = date.today() - timedelta(days=3)


def _fresh_fund_code() -> str:
    """Unique 6-digit leading-5 CN fund code per env instance (the holdings
    snapshot unique key is (fund_code, report_date, stock_code), and the env
    fixture is function-scoped)."""
    return f"51{uuid.uuid4().int % 10000:04d}"


class _SettablePipeline:
    """fund_nav_rt writes use pipeline.set(...); MockRedis's pipeline only
    covers list/zset ops."""

    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def set(self, key, value, ex=None, nx=False):
        self._ops.append((key, value, ex))
        return self

    async def execute(self):
        results = []
        for key, value, ex in self._ops:
            results.append(await self._redis.set(key, value, ex=ex))
        self._ops = []
        return results


class IntradayRedis(MockRedis):
    def pipeline(self, transaction=True):
        return _SettablePipeline(self)


@pytest.fixture
def fake_redis():
    return IntradayRedis()


@pytest.fixture
def patched_intraday_redis(fake_redis):
    """Patch every Redis entry point the cycle touches: module-level helpers
    in fund_holdings plus the client factory in both modules."""

    async def _get(key):
        return fake_redis._data.get(key)

    async def _set(key, value, ex=None):
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        fake_redis._data[key] = value
        if ex is not None:
            fake_redis._expiry[key] = fake_redis._clock + ex

    async def _delete(key):
        fake_redis._data.pop(key, None)
        fake_redis._expiry.pop(key, None)

    async def _client():
        return fake_redis

    with (
        patch("app.services.fund_holdings.redis_get", new=_get),
        patch("app.services.fund_holdings.redis_set", new=_set),
        patch("app.services.fund_holdings.redis_delete", new=_delete),
        patch("app.services.fund_holdings.get_redis_client", new=_client),
        patch("app.services.fund_intraday.get_redis_client", new=_client),
    ):
        yield fake_redis


class _Tick:
    def __init__(self, change_percent, quote_status="realtime"):
        self.change_percent = change_percent
        self.quote_status = quote_status


def _governor():
    gov = AsyncMock()
    gov.acquire = AsyncMock(return_value=True)
    gov.release = MagicMock()
    gov.report_result = AsyncMock()
    gov.pick_chain = AsyncMock(side_effect=lambda market: ["tencent_qt", "sina_hq"])
    return gov


@pytest.fixture(autouse=True)
def _clean_cycle_state():
    fund_intraday.reset_cycle_state()
    yield
    fund_intraday.reset_cycle_state()


@pytest.fixture
async def intraday_env(db_session):
    """Tenant + user + fund symbol + official anchor + holdings + watchlist."""
    suffix = uuid.uuid4().hex[:8]
    fund_code = _fresh_fund_code()
    tenant = Tenant(name="Intraday Tenant", slug=f"intraday-{suffix}")
    db_session.add(tenant)
    await db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"intraday-{suffix}@example.com",
        name="Intraday User",
        sso_provider="github",
        sso_provider_id=uuid.uuid4().hex,
    )
    db_session.add(user)

    symbol = FinanceSymbol(tenant_id=tenant.id, symbol=f"{fund_code}.SS", name="测试沪深基金", type="fund", market="CN")
    db_session.add(symbol)
    await db_session.flush()

    db_session.add(
        FundNAVEstimate(
            tenant_id=tenant.id,
            symbol_id=symbol.id,
            nav_official=4.5,
            nav_official_date=ANCHOR_DATE,
            estimate_method="official",
            estimate_timestamp=datetime.now(UTC),
        )
    )
    db_session.add_all(
        [
            FundHoldingSnapshot(
                tenant_id=SYSTEM_TENANT_ID,
                fund_code=fund_code,
                symbol_id=symbol.id,
                report_date=REPORT_DATE,
                stock_code="600519",
                stock_name="贵州茅台",
                market="CN",
                secid="1.600519",
                weight_percent=60.0,
                fetched_at=datetime.now(UTC),
            ),
            FundHoldingSnapshot(
                tenant_id=SYSTEM_TENANT_ID,
                fund_code=fund_code,
                symbol_id=symbol.id,
                report_date=REPORT_DATE,
                stock_code="000858",
                stock_name="五粮液",
                market="CN",
                secid="0.000858",
                weight_percent=20.0,
                fetched_at=datetime.now(UTC),
            ),
        ]
    )
    db_session.add(
        FundHoldingsMeta(
            tenant_id=SYSTEM_TENANT_ID,
            fund_code=fund_code,
            symbol_id=symbol.id,
            latest_report_date=REPORT_DATE,
            top10_weight_sum=80.0,
            holdings_count=2,
            disclosure_status="ok",
            last_fetched_at=datetime.now(UTC),
        )
    )

    watch_item = WatchlistItem(tenant_id=tenant.id, user_id=user.id, symbol_id=symbol.id, display_order=0)
    db_session.add(watch_item)
    await db_session.commit()

    return {"tenant": tenant, "user": user, "symbol": symbol, "fund_code": fund_code}


class TestCycleEndToEnd:
    async def test_cycle_computes_and_pushes_tenant_filtered(self, intraday_env, patched_intraday_redis):
        from tests.conftest import test_session_factory

        fake_redis = patched_intraday_redis
        fund_code = intraday_env["fund_code"]
        tenant_a = str(intraday_env["tenant"].id)
        tenant_b = str(uuid.uuid4())
        # tenant A online for finance; tenant B online but follows nothing.
        fake_redis._data[RedisKeys.sse_connected_tenants_finance_key()] = {tenant_a, tenant_b}

        quotes = {"1.600519": _Tick(1.0), "0.000858": _Tick(2.0)}
        pushed = []

        async def _capture_push(category, event_type, data, tenant_id, **kwargs):
            pushed.append({"category": category, "event_type": event_type, "data": data, "tenant_id": tenant_id})

        async with test_session_factory() as session:
            svc = FundIntradayService(db=session, governor=_governor())
            with (
                # Hermetic union: the real followed-union is cross-tenant and
                # picks up other tests' committed fund watchlist rows (e2e's
                # 519999), so inject exactly this env's fund for tenant A.
                patch(
                    "app.services.fund_holdings.FundHoldingsService.get_followed_codes",
                    new_callable=AsyncMock,
                    return_value=([fund_code], {tenant_a: {fund_code}}),
                ),
                patch("app.services.fund_intraday.fetch_quotes", new_callable=AsyncMock, return_value=(quotes, [])),
                patch("app.services.fund_intraday.event_router") as mock_router,
            ):
                mock_router.push_event = AsyncMock(side_effect=_capture_push)
                summary = await svc.compute_cycle()

        assert summary["codes"] == 1
        result = summary["results"][0]
        assert result["symbol"] == fund_code
        assert result["estimate_method"] == "holdings_weighted"
        # 0.6×1.0 + 0.2×2.0 = 1.0%  →  4.5 × 1.01
        assert abs(result["estimate_change_percent"] - 1.0) < 1e-9
        assert abs(result["nav_estimate"] - 4.545) < 1e-9
        assert result["coverage_percent"] == 80.0
        assert result["quote_status"] == "realtime"
        assert result["delayed_markets"] == []

        # tenant A gets only its code; tenant B online but empty → skipped;
        # system tenant always receives the full array.
        tenants_pushed = {p["tenant_id"] for p in pushed}
        assert tenants_pushed == {tenant_a, str(SYSTEM_TENANT_ID)}
        for p in pushed:
            assert str(p["event_type"]) == "nav_batch_update" or p["event_type"].value == "nav_batch_update"
            assert [e["symbol"] for e in p["data"]] == [fund_code]

        # realtime cache written through the pipeline with the 12s TTL
        rt_key = RedisKeys.fund_nav_rt_key(fund_code)
        assert rt_key in fake_redis._data
        assert fake_redis._expiry[rt_key] == fake_redis._clock + 12
        stored = json.loads(fake_redis._data[rt_key])
        assert stored["estimate_method"] == "holdings_weighted"

    async def test_empty_union_zero_upstream_requests(self, db_session, patched_intraday_redis):
        """No followed funds → the cycle short-circuits before any quote work.

        The union SQL itself (zero rows when no watchlist funds exist) is
        covered at the unit layer; here the shared integration DB already
        contains other tests' watchlist rows, so the union result is injected
        to verify the short circuit end-to-end (no fetch, no push, no cache).
        """
        from app.services.fund_holdings import FundHoldingsService
        from tests.conftest import test_session_factory

        async with test_session_factory() as session:
            svc = FundIntradayService(db=session, governor=_governor())
            with (
                patch.object(
                    FundHoldingsService,
                    "get_followed_codes",
                    new_callable=AsyncMock,
                    return_value=([], {}),
                ),
                patch("app.services.fund_intraday.fetch_quotes", new_callable=AsyncMock) as mock_fetch,
            ):
                summary = await svc.compute_cycle()

        assert summary == {"codes": 0, "results": [], "pushed_tenants": 0}
        mock_fetch.assert_not_awaited()
        assert patched_intraday_redis._data.get(RedisKeys.fund_nav_rt_key("anything")) is None


def _flush_entry(fund_code, method="holdings_weighted", status="realtime", nav=4.52):
    return {
        "symbol": fund_code,
        "name": "测试沪深基金",
        "nav_official": 4.5,
        "nav_official_date": ANCHOR_DATE.isoformat(),
        "nav_estimate": nav,
        "estimate_change_percent": 0.44,
        "estimate_method": method,
        "coverage_percent": 80.0,
        "holdings_report_date": REPORT_DATE.isoformat(),
        "quote_status": status,
        "delayed_markets": [],
        "holdings_stale": False,
        "estimate_timestamp": datetime.now(UTC).isoformat(),
    }


class TestDownsampleFlush:
    async def _rows(self, session, symbol_id, method):
        from sqlalchemy import select

        result = await session.execute(
            select(FundNAVEstimate).where(
                FundNAVEstimate.symbol_id == symbol_id,
                FundNAVEstimate.estimate_method == method,
            )
        )
        return list(result.scalars().all())

    async def test_throttle_window_release_and_state_switch(self, intraday_env, patched_intraday_redis):
        from tests.conftest import test_session_factory

        fake_redis = patched_intraday_redis
        fund_code = intraday_env["fund_code"]
        symbol_id = intraday_env["symbol"].id
        ids_per_code = {fund_code: [symbol_id]}

        async with test_session_factory() as session:
            svc = FundIntradayService(db=session, governor=_governor())

            # 1. first write goes through and anchors the throttle window
            await svc._flush_results([_flush_entry(fund_code)], ids_per_code)
            rows = await self._rows(session, symbol_id, "holdings_weighted")
            assert len(rows) == 1
            assert float(rows[0].nav_estimate) == 4.52

            # 2. within the window → throttled, row untouched (value sentinel
            # instead of estimate_timestamp: sqlite round-trips naive datetimes)
            await svc._flush_results([_flush_entry(fund_code, nav=4.77)], ids_per_code)
            rows = await self._rows(session, symbol_id, "holdings_weighted")
            assert len(rows) == 1
            assert float(rows[0].nav_estimate) == 4.52

            # 3. after the gap expires → same-row override with the new value
            fake_redis.advance(61)
            await svc._flush_results([_flush_entry(fund_code, nav=4.99)], ids_per_code)
            rows = await self._rows(session, symbol_id, "holdings_weighted")
            assert len(rows) == 1
            assert float(rows[0].nav_estimate) == 4.99

            # 4. method switch forces a write even inside the window — a new
            # row family (index_tracking), preserving the downgrade trail
            await svc._flush_results([_flush_entry(fund_code, method="index_tracking", status="delayed")], ids_per_code)
            assert len(await self._rows(session, symbol_id, "index_tracking")) == 1
            assert len(await self._rows(session, symbol_id, "holdings_weighted")) == 1


@pytest.fixture
def api_env(app_with_overrides):
    app, mock_redis = app_with_overrides
    return TestClient(app), mock_redis


class TestBatchApi:
    def _headers(self):
        headers, _tenant, _user = make_auth_header(role="admin")
        return headers

    def test_unknown_code_returns_error_entry_not_404(self, api_env):
        client, _redis = api_env
        resp = client.get("/api/v1/finance/fund-nav/batch?symbols=ZZZZZ", headers=self._headers())
        assert resp.status_code == 200
        entry = resp.json()["data"][0]
        assert entry["symbol"] == "ZZZZZ"
        assert entry["error"] == "unrecognized fund code"

    def test_over_50_symbols_rejected_with_400(self, api_env):
        client, _redis = api_env
        symbols = ",".join(["510300"] * 50 + ["000961"])  # 51 entries
        resp = client.get(f"/api/v1/finance/fund-nav/batch?symbols={symbols}", headers=self._headers())
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    def test_rt_cache_hit_echoed(self, api_env):
        client, redis = api_env
        payload = {
            "symbol": "510300",
            "name": "沪深300ETF",
            "nav_official": 4.5,
            "nav_official_date": "2026-08-28",
            "nav_estimate": 4.52,
            "estimate_change_percent": 0.44,
            "estimate_method": "holdings_weighted",
            "coverage_percent": 70.0,
            "holdings_report_date": "2026-06-30",
            "quote_status": "realtime",
            "delayed_markets": [],
            "holdings_stale": False,
            "estimate_timestamp": datetime.now(UTC).isoformat(),
        }
        redis._data[RedisKeys.fund_nav_rt_key("510300")] = json.dumps(payload)
        try:
            resp = client.get("/api/v1/finance/fund-nav/batch?symbols=510300", headers=self._headers())
            assert resp.status_code == 200
            entry = resp.json()["data"][0]
            assert entry["estimate_method"] == "holdings_weighted"
            assert entry["nav_estimate"] == 4.52
            assert entry["coverage_percent"] == 70.0
        finally:
            redis._data.pop(RedisKeys.fund_nav_rt_key("510300"), None)

    def test_miss_falls_back_and_fires_lazy_ingest_hook(self, api_env):
        client, _redis = api_env
        with patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn:
            resp = client.get("/api/v1/finance/fund-nav/batch?symbols=005827", headers=self._headers())
        assert resp.status_code == 200
        entry = resp.json()["data"][0]
        assert entry["estimate_method"] == "latest_official"
        assert entry["error"] == "fund symbol not found"
        mock_spawn.assert_called_once_with("005827")


@pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="str ids bound against UUID columns require PostgreSQL coercion",
)
class TestSearchFundRegistration:
    async def test_bare_fund_code_registered_and_variant_resolves(self, db_session):
        """Search auto-registers OTC-style fund codes, and the watchlist add
        accepts the other spelling of the same fund (§M2 phase A)."""
        from sqlalchemy import select

        from app.services.finance import FinanceService

        suffix = uuid.uuid4().hex[:8]
        fund_code = _fresh_fund_code()
        fund_symbol = f"{fund_code}.SS"
        tenant = Tenant(name="Search Fund Tenant", slug=f"search-fund-{suffix}")
        db_session.add(tenant)
        await db_session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"search-fund-{suffix}@example.com",
            name="Search Fund User",
            sso_provider="github",
            sso_provider_id=uuid.uuid4().hex,
        )
        db_session.add(user)
        await db_session.commit()

        svc = FinanceService(db=db_session, redis=None)
        tenant_id = str(tenant.id)

        async def _get(key):
            return None

        async def _set(key, value, ex=None):
            pass

        async def _delete(key):
            pass

        # Empty local DB + empty external search → the fund-code fallback
        # registers the symbol under the requesting tenant.
        with (
            patch("app.services.finance.redis_get", new=_get),
            patch("app.services.finance.redis_set", new=_set),
            patch("app.services.finance.redis_delete", new=_delete),
            patch.object(FinanceService, "_search_symbols_external", new_callable=AsyncMock, return_value=[]),
        ):
            page = await svc.search_symbols(tenant_id, fund_code, type="fund")

        assert page["meta"]["total"] == 1
        assert page["data"][0]["symbol"] == fund_code
        assert page["data"][0]["type"] == "fund"

        registered = (
            await db_session.execute(
                select(FinanceSymbol).where(
                    FinanceSymbol.tenant_id == tenant.id,
                    FinanceSymbol.symbol == fund_code,
                )
            )
        ).scalar_one()
        assert registered.type == "fund"

        # Variant spelling: adding the suffixed form resolves the bare symbol.
        with (
            patch("app.services.finance.redis_get", new=_get),
            patch("app.services.finance.redis_delete", new=_delete),
            patch.object(FinanceService, "_get_cached_quote", new_callable=AsyncMock, return_value=None),
            patch("app.services.fund_holdings.invalidate_followed_codes_cache", new_callable=AsyncMock),
            patch("app.services.fund_holdings.spawn_holdings_ingestion") as mock_spawn,
        ):
            item = await svc.add_to_watchlist(tenant_id, str(user.id), {"symbol": fund_symbol})

        assert item["symbol"] == fund_code  # resolved to the registered symbol
        mock_spawn.assert_called_once_with(fund_code)

        rows = (
            (
                await db_session.execute(
                    select(WatchlistItem).where(
                        WatchlistItem.tenant_id == tenant.id,
                        WatchlistItem.user_id == user.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1 and rows[0].symbol_id == registered.id
