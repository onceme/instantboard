"""Unit tests for fund holdings ingestion (fund-intraday-nav.md §5).

Covers the f10 jjcc parser (apidata extraction, multi-period newest-wins,
secid + market inference, weight/shares classification), the disclosure meta
state machine (ok / stale / anomalous), the budget-gated ingest flow,
needs_ingestion gating, and patient budget pacing for the daily cron.
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.fund_holdings import (
    FUND_HOLDINGS_ANOMALOUS_DAYS,
    FundHoldingsService,
    _normalize_fund_code_shared,
    parse_f10_jjcc,
)


def _apidata(content_html: str) -> str:
    return f'var apidata= {{ content:"{content_html}",arryear:2026,foo:"bar"}}'


def _table(rows_html: str) -> str:
    return (
        "<table class='w782 comm hold'>"
        "<thead><tr><th>代码</th><th>名称</th><th>涨跌幅</th><th>股数</th><th>占比</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table>"
    )


def _row(secid: str, code: str, name: str, day_change: str, shares: str, weight: str) -> str:
    return (
        "<tr>"
        f"<td class='tol'><a href='//quote.eastmoney.com/unify/r/{secid}'>{code}</a></td>"
        f"<td class='tol'><a href='//f10.example/{code}'>{name}</a></td>"
        f"<td class='tor'>{day_change}</td>"
        f"<td class='tor'>{shares}</td>"
        f"<td class='tor'>{weight}</td>"
        "</tr>"
    )


NEWEST_ROWS = _table(
    _row("1.600519", "600519", "贵州茅台", "1.20%", "1,200", "8.52%")
    + _row("0.000858", "000858", "五粮液", "-0.80%", "900", "6.10%")
    + _row("116.00700", "00700", "腾讯控股", "0.50%", "50,000", "4.00%")
)
OLDER_ROWS = _table(_row("1.600519", "600519", "贵州茅台", "0.10%", "800", "9.99%"))


class TestParseF10Jjcc:
    def test_multi_period_newest_wins(self):
        """One payload carries several report periods; only the newest is kept
        (§5.3 wholesale replacement source)."""
        html = (
            "<div class='box'><h4 class='title'>2026-06-30 股票持仓</h4>"
            f"{NEWEST_ROWS}</div>"
            "<div class='box'><h4 class='title'>2025-12-31 股票持仓</h4>"
            f"{OLDER_ROWS}</div>"
        )
        result = parse_f10_jjcc(_apidata(html))
        assert result.report_date == date(2026, 6, 30)
        assert len(result.rows) == 3
        by_code = {r.stock_code: r for r in result.rows}
        assert by_code["600519"].weight_percent == 8.52  # newest period value, not 9.99

    def test_period_order_independent(self):
        """Newest wins regardless of box order in the payload."""
        html = (
            "<div class='box'><h4 class='title'>2025-12-31 股票持仓</h4>"
            f"{OLDER_ROWS}</div>"
            "<div class='box'><h4 class='title'>2026-06-30 股票持仓</h4>"
            f"{NEWEST_ROWS}</div>"
        )
        result = parse_f10_jjcc(_apidata(html))
        assert result.report_date == date(2026, 6, 30)

    def test_secid_and_market_inference(self):
        result = parse_f10_jjcc(_apidata(f"<h4>2026-06-30 持仓</h4>{NEWEST_ROWS}"))
        by_code = {r.stock_code: r for r in result.rows}
        assert by_code["600519"].secid == "1.600519"
        assert by_code["600519"].market == "CN"
        assert by_code["000858"].secid == "0.000858"
        assert by_code["000858"].market == "CN"
        assert by_code["00700"].secid == "116.00700"
        assert by_code["00700"].market == "HK"
        assert by_code["00700"].shares_held == 50000.0

    def test_shares_and_names(self):
        result = parse_f10_jjcc(_apidata(f"<h4>2026-06-30 持仓</h4>{NEWEST_ROWS}"))
        by_code = {r.stock_code: r for r in result.rows}
        assert by_code["600519"].shares_held == 1200.0
        assert by_code["600519"].stock_name == "贵州茅台"
        assert by_code["00700"].stock_name == "腾讯控股"

    def test_weight_is_last_percent_cell(self):
        """Day change is also a % cell — the LAST percent column is the NAV
        weight (§5.3)."""
        result = parse_f10_jjcc(_apidata(f"<h4>2026-06-30 持仓</h4>{NEWEST_ROWS}"))
        by_code = {r.stock_code: r for r in result.rows}
        assert by_code["600519"].weight_percent == 8.52  # not the 1.20% day change

    def test_rows_without_percent_are_dropped(self):
        html = "<h4>2026-06-30 持仓</h4>" + _table(
            "<tr><td><a href='//quote.eastmoney.com/unify/r/1.600519'>600519</a></td><td>贵州茅台</td><td>--</td></tr>"
        )
        result = parse_f10_jjcc(_apidata(html))
        assert result.rows == []

    def test_missing_content_is_empty(self):
        assert parse_f10_jjcc("").rows == []
        assert parse_f10_jjcc("var apidata={foo:1}").rows == []
        assert parse_f10_jjcc(_apidata("<div>nothing here</div>")).rows == []


class TestDisclosureStateMachine:
    def _svc(self):
        return FundHoldingsService(db=AsyncMock(), governor=AsyncMock())

    def test_fresh_report_is_ok(self):
        report = date.today() - _days(30)
        assert self._svc()._disclosure_status(report, 10) == "ok"

    def test_mid_age_report_is_stale(self):
        report = date.today() - _days(200)
        assert self._svc()._disclosure_status(report, 10) == "stale"

    def test_zero_holdings_is_anomalous(self):
        report = date.today() - _days(10)
        assert self._svc()._disclosure_status(report, 0) == "anomalous"

    def test_missing_report_date_is_anomalous(self):
        assert self._svc()._disclosure_status(None, 10) == "anomalous"

    def test_very_old_report_is_anomalous(self):
        report = date.today() - _days(FUND_HOLDINGS_ANOMALOUS_DAYS + 1)
        assert self._svc()._disclosure_status(report, 10) == "anomalous"

    def test_boundary_freshness(self):
        fresh_days = 120
        ok_report = date.today() - _days(fresh_days)
        stale_report = date.today() - _days(fresh_days + 1)
        assert self._svc()._disclosure_status(ok_report, 10) == "ok"
        assert self._svc()._disclosure_status(stale_report, 10) == "stale"


class TestNeedsIngestion:
    async def test_no_meta_needs_ingestion(self):
        db = AsyncMock()
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute = AsyncMock(return_value=count_result)
        svc = FundHoldingsService(db=db, governor=AsyncMock())
        assert await svc.needs_ingestion("510300") is True

    async def test_healthy_meta_does_not_need_ingestion(self):
        db = AsyncMock()
        meta = MagicMock()
        meta.last_error = None
        meta.latest_report_date = date.today()
        meta_result = MagicMock()
        meta_result.scalar_one_or_none.return_value = meta
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        db.execute = AsyncMock(side_effect=[count_result, meta_result])
        svc = FundHoldingsService(db=db, governor=AsyncMock())
        assert await svc.needs_ingestion("510300") is False

    async def test_errored_meta_needs_retry(self):
        db = AsyncMock()
        meta = MagicMock()
        meta.last_error = "fetch failed"
        meta.latest_report_date = None
        meta_result = MagicMock()
        meta_result.scalar_one_or_none.return_value = meta
        count_result = MagicMock()
        count_result.scalar.return_value = 1
        db.execute = AsyncMock(side_effect=[count_result, meta_result])
        svc = FundHoldingsService(db=db, governor=AsyncMock())
        assert await svc.needs_ingestion("510300") is True

    async def test_codes_needing_ingestion_batched(self):
        db = AsyncMock()
        healthy = MagicMock()
        healthy.fund_code = "510300"
        healthy.last_error = None
        healthy.latest_report_date = date.today()
        errored = MagicMock()
        errored.fund_code = "005827"
        errored.last_error = "failed"
        errored.latest_report_date = None
        result = MagicMock()
        result.scalars.return_value.all.return_value = [healthy, errored]
        db.execute = AsyncMock(return_value=result)
        svc = FundHoldingsService(db=db, governor=AsyncMock())
        needing = await svc.codes_needing_ingestion(["510300", "005827", "110011"])
        assert needing == {"005827", "110011"}  # errored + never-ingested


class TestIngestFund:
    def _svc(self, db):
        governor = AsyncMock()
        governor.acquire = AsyncMock(return_value=True)
        governor.report_result = AsyncMock()
        governor.release = MagicMock()
        return FundHoldingsService(db=db, governor=governor), governor

    def _mock_db(self):
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        empty_result = MagicMock()
        empty_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=empty_result)
        return db

    def _mock_response(self, text: str, status_code: int = 200):
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = text
        return resp

    async def test_ingest_replaces_snapshot_and_writes_meta(self):
        db = self._mock_db()
        svc, governor = self._svc(db)
        html = f"<h4 class='title'>2026-06-30 股票持仓</h4>{NEWEST_ROWS}"
        with (
            patch("app.services.fund_holdings.redis_set", new_callable=AsyncMock) as mock_cache,
            patch("httpx.AsyncClient") as mock_client_cls,
        ):
            client = AsyncMock()
            client.get = AsyncMock(return_value=self._mock_response(_apidata(html)))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client
            assert await svc.ingest_fund("510300") is True

        db.commit.assert_awaited()
        governor.release.assert_called_with("em_f10_holdings")
        # 3 snapshot rows + 1 meta row
        assert db.add.call_count == 4
        added_names = [c[0][0].__class__.__name__ for c in db.add.call_args_list]
        assert added_names.count("FundHoldingSnapshot") == 3
        assert added_names.count("FundHoldingsMeta") == 1
        mock_cache.assert_awaited()

    async def test_ingest_records_error_on_budget_denial(self):
        db = self._mock_db()
        svc, _ = self._svc(db)
        svc.governor.acquire = AsyncMock(return_value=False)
        assert await svc.ingest_fund("510300") is False
        # meta error recorded + committed (daily retry relies on it)
        db.commit.assert_awaited()
        meta_obj = db.add.call_args_list[-1][0][0]
        assert meta_obj.__class__.__name__ == "FundHoldingsMeta"
        assert "fetch failed" in (meta_obj.last_error or "")

    async def test_ingest_records_error_on_http_failure(self):
        db = self._mock_db()
        svc, _ = self._svc(db)
        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=self._mock_response("", status_code=404))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client
            assert await svc.ingest_fund("510300") is False
        meta_obj = db.add.call_args_list[-1][0][0]
        assert "fetch failed" in (meta_obj.last_error or "")
        # a 404 is a per-fund miss, NOT an upstream denial (§5.4): reported with
        # ok=False but is_denial() must not trip the cooldown for it.
        denial_calls = [c for c in svc.governor.report_result.await_args_list if len(c.args) >= 3 and c.args[2] == 404]
        assert denial_calls and denial_calls[0].args[1] is False
        from app.services.upstream_budget import is_denial

        assert is_denial(False, 404) is False

    async def test_ingest_unparsable_payload_keeps_old_data_with_error(self):
        db = self._mock_db()
        svc, _ = self._svc(db)
        with patch("httpx.AsyncClient") as mock_client_cls:
            client = AsyncMock()
            client.get = AsyncMock(return_value=self._mock_response(_apidata("<div>no tables</div>")))
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = client
            assert await svc.ingest_fund("510300") is False
        meta_obj = db.add.call_args_list[-1][0][0]
        assert "unparsable" in (meta_obj.last_error or "")


class TestPatientPacing:
    async def test_patient_acquire_waits_for_budget(self):
        db = AsyncMock()
        governor = AsyncMock()
        governor.acquire = AsyncMock(side_effect=[False, False, True])
        svc = FundHoldingsService(db=db, governor=governor)
        with patch("app.services.fund_holdings.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            assert await svc._acquire_slot("em_f10_holdings", patient=True) is True
        assert governor.acquire.await_count == 3
        assert mock_sleep.await_count == 2

    async def test_non_patient_returns_immediately(self):
        db = AsyncMock()
        governor = AsyncMock()
        governor.acquire = AsyncMock(return_value=False)
        svc = FundHoldingsService(db=db, governor=governor)
        assert await svc._acquire_slot("em_f10_holdings", patient=False) is False
        assert governor.acquire.await_count == 1


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


class TestNormalizeFundCode:
    def test_suffixes_stripped(self):
        assert _normalize_fund_code_shared("510300.SS") == "510300"
        assert _normalize_fund_code_shared("159915.SZ") == "159915"
        assert _normalize_fund_code_shared("110020.OF") == "110020"
        assert _normalize_fund_code_shared("510300") == "510300"

    def test_invalid_codes_rejected(self):
        assert _normalize_fund_code_shared("AAPL") is None
        assert _normalize_fund_code_shared("12345") is None
        assert _normalize_fund_code_shared("") is None
