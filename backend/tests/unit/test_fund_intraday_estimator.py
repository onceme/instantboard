"""Unit tests for FundIntradayService estimation (fund-intraday-nav.md §4).

Covers the holdings-weighted formula (including the unknown-position 0%
assumption and its bias direction), the tiered decision table with its
120-day / 30% boundaries, quote_status / delayed_markets derivation, and the
degradation paths (zero resolved quotes → index tracking → latest_official).
"""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from app.services.fund_intraday import FundIntradayService

TODAY = date(2026, 8, 31)
FRESH_DAYS = 120
MIN_COVERAGE = 30.0


class _Tick:
    """Minimal stand-in for quote_batch.QuoteTick."""

    def __init__(self, change_percent: float | None, quote_status: str = "realtime"):
        self.change_percent = change_percent
        self.quote_status = quote_status


def _service() -> FundIntradayService:
    return FundIntradayService(db=AsyncMock(), governor=AsyncMock())


def _holdings(rows, report_date="2026-06-30", status="ok", coverage=None) -> dict:
    cov = coverage if coverage is not None else round(sum(r["weight_percent"] for r in rows), 4)
    return {
        "fund_code": "510300",
        "report_date": report_date,
        "coverage_percent": cov,
        "disclosure_status": status,
        "holdings": rows,
    }


CN_ROWS = [
    {"stock_code": "600519", "market": "CN", "weight_percent": 40.0, "secid": "1.600519"},
    {"stock_code": "000858", "market": "CN", "weight_percent": 30.0, "secid": "0.000858"},
]
CN_HK_ROWS = CN_ROWS + [
    {"stock_code": "00700", "market": "HK", "weight_percent": 10.0, "secid": "116.00700"},
]
ANCHOR = (4.5, date(2026, 8, 28))


class TestTierPlanning:
    def test_fresh_high_coverage_is_holdings_weighted(self):
        svc = _service()
        hp = _holdings(CN_ROWS, report_date="2026-06-30", coverage=70.0)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "holdings_weighted"

    def test_boundary_age_exactly_fresh_days_still_holdings_weighted(self):
        svc = _service()
        boundary = date(2026, 5, 3)  # (2026-08-31 - 2026-05-03) == 120 days
        assert (TODAY - boundary).days == 120
        hp = _holdings(CN_ROWS, report_date=boundary.isoformat(), coverage=70.0)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "holdings_weighted"

    def test_one_day_over_fresh_days_falls_back_to_index(self):
        svc = _service()
        boundary = date(2026, 5, 2)  # age 121 > 120
        assert (TODAY - boundary).days == 121
        hp = _holdings(CN_ROWS, report_date=boundary.isoformat(), coverage=70.0)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "index_tracking"
        assert svc._plan_tier(hp, None, FRESH_DAYS, MIN_COVERAGE, TODAY) == "latest_official"

    def test_boundary_coverage_exactly_minimum_still_holdings_weighted(self):
        svc = _service()
        hp = _holdings(CN_ROWS, coverage=30.0)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "holdings_weighted"

    def test_coverage_below_minimum_falls_back(self):
        svc = _service()
        hp = _holdings(CN_ROWS, coverage=29.99)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "index_tracking"

    def test_no_holdings_no_binding_is_latest_official(self):
        svc = _service()
        assert svc._plan_tier(None, None, FRESH_DAYS, MIN_COVERAGE, TODAY) == "latest_official"

    def test_no_holdings_with_binding_is_index_tracking(self):
        svc = _service()
        assert svc._plan_tier(None, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "index_tracking"

    def test_anomalous_disclosure_never_holdings_weighted(self):
        svc = _service()
        hp = _holdings(CN_ROWS, report_date="2022-06-30", status="anomalous", coverage=70.0)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "index_tracking"
        assert svc._plan_tier(hp, None, FRESH_DAYS, MIN_COVERAGE, TODAY) == "latest_official"

    def test_empty_rows_never_holdings_weighted(self):
        svc = _service()
        hp = _holdings([], coverage=0.0)
        assert svc._plan_tier(hp, ("000300.SS", 1.0), FRESH_DAYS, MIN_COVERAGE, TODAY) == "index_tracking"


class TestWeightedEstimate:
    def test_weighted_formula_mixed_realtime_and_delayed(self):
        """Σ (w/100 × chg): 0.4×2 + 0.3×(-1) + 0.1×1.5 = 0.65."""
        svc = _service()
        quotes = {
            "1.600519": _Tick(2.0),
            "0.000858": _Tick(-1.0),
            "116.00700": _Tick(1.5, "delayed"),
        }
        hp = _holdings(CN_HK_ROWS)
        result = svc._estimate_one(
            "510300",
            "沪深300ETF",
            hp,
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            quotes,
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result["estimate_method"] == "holdings_weighted"
        assert abs(result["estimate_change_percent"] - 0.65) < 1e-9
        # nav_estimate = 4.5 × (1 + 0.65%)
        assert abs(result["nav_estimate"] - round(4.5 * 1.0065, 4)) < 1e-9
        assert result["coverage_percent"] == 80.0
        assert result["quote_status"] == "mixed"
        assert result["delayed_markets"] == ["HK"]
        assert result["holdings_stale"] is False

    def test_unknown_positions_assume_zero_change(self):
        """Missing quotes contribute exactly 0 (the unknown-position assumption
        of §4.1), so partial data never fabricates movement."""
        svc = _service()
        quotes = {"1.600519": _Tick(2.0)}  # only one of two CN legs resolved
        hp = _holdings(CN_ROWS, coverage=70.0)

        from datetime import UTC, datetime

        result = svc._estimate_one(
            "510300",
            "沪深300ETF",
            hp,
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            quotes,
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        # 0.4×2 + 0.3×0 = 0.8 — the unknown 30% leg contributes 0.
        assert abs(result["estimate_change_percent"] - 0.8) < 1e-9

    def test_bias_direction_up_day_underestimates(self):
        """All-up day with coverage < 100%: the estimate rises LESS than the
        average constituent move (unknown positions pinned at 0), and on an
        all-down day it falls less — the bias direction the accuracy badge
        exists to disclose."""
        svc = _service()

        from datetime import UTC, datetime

        now = datetime.now(UTC)
        rows_full_rise = [{"stock_code": "600519", "market": "CN", "weight_percent": 50.0, "secid": "1.600519"}]
        up_result = svc._estimate_one(
            "510300",
            "t",
            _holdings(rows_full_rise, coverage=50.0),
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            {"1.600519": _Tick(2.0)},
            now,
            TODAY,
            FRESH_DAYS,
        )
        assert 0 < up_result["estimate_change_percent"] < 2.0  # up day: biased low

        down_result = svc._estimate_one(
            "510300",
            "t",
            _holdings(rows_full_rise, coverage=50.0),
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            {"1.600519": _Tick(-2.0)},
            now,
            TODAY,
            FRESH_DAYS,
        )
        assert -2.0 < down_result["estimate_change_percent"] < 0  # down day: biased high

    def test_coverage_capped_at_100(self):
        """Σw above 100 (dirty disclosure data) is capped, never inflated."""
        svc = _service()
        rows = [
            {"stock_code": "600519", "market": "CN", "weight_percent": 80.0, "secid": "1.600519"},
            {"stock_code": "000858", "market": "CN", "weight_percent": 80.0, "secid": "0.000858"},
        ]

        from datetime import UTC, datetime

        result = svc._estimate_one(
            "510300",
            "t",
            _holdings(rows, coverage=160.0),
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            {"1.600519": _Tick(1.0), "0.000858": _Tick(1.0)},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result["coverage_percent"] == 100.0

    def test_empty_snapshot_degrades(self):
        svc = _service()

        from datetime import UTC, datetime

        result = svc._estimate_one(
            "510300",
            "t",
            _holdings([]),
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            {},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        # No binding → latest_official.
        assert result["estimate_method"] == "latest_official"
        assert result["quote_status"] == "frozen"


class TestQuoteStatusDerivation:
    def _estimate(self, rows, quotes):
        from datetime import UTC, datetime

        svc = _service()
        return svc._estimate_one(
            "510300",
            "t",
            _holdings(rows),
            ANCHOR,
            None,
            "holdings_weighted",
            None,
            quotes,
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )

    def test_cn_only_is_realtime(self):
        result = self._estimate(CN_ROWS, {"1.600519": _Tick(1.0), "0.000858": _Tick(-1.0)})
        assert result["quote_status"] == "realtime"
        assert result["delayed_markets"] == []

    def test_hk_only_is_delayed(self):
        rows = [{"stock_code": "00700", "market": "HK", "weight_percent": 40.0, "secid": "116.00700"}]
        result = self._estimate(rows, {"116.00700": _Tick(1.0, "delayed")})
        assert result["quote_status"] == "delayed"
        assert result["delayed_markets"] == ["HK"]

    def test_missing_quote_leg_counts_as_delayed(self):
        result = self._estimate(CN_HK_ROWS, {"1.600519": _Tick(1.0)})
        # HK leg unresolved and one CN leg unresolved → delayed markets, mixed
        # only when at least one realtime leg also resolved.
        assert "HK" in result["delayed_markets"]
        assert result["quote_status"] == "mixed"

    def test_all_missing_degrades(self):
        result = self._estimate(CN_HK_ROWS, {})
        assert result["estimate_method"] == "latest_official"
        assert result["quote_status"] == "frozen"


class TestIndexTrackingFallback:
    def test_index_tracking_applies_ratio(self):
        from datetime import UTC, datetime

        svc = _service()
        result = svc._estimate_one(
            "510300",
            "t",
            None,
            ANCHOR,
            ("000300.SS", 0.95),
            "index_tracking",
            "CN:000300",
            {"CN:000300": _Tick(1.2)},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result["estimate_method"] == "index_tracking"
        assert abs(result["estimate_change_percent"] - 1.14) < 1e-9
        assert result["quote_status"] == "realtime"
        assert result["coverage_percent"] is None

    def test_index_quote_missing_degrades_to_official(self):
        from datetime import UTC, datetime

        svc = _service()
        result = svc._estimate_one(
            "510300",
            "t",
            None,
            ANCHOR,
            ("000300.SS", 1.0),
            "index_tracking",
            "CN:000300",
            {},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result["estimate_method"] == "latest_official"
        assert result["nav_estimate"] == 4.5
        assert result["estimate_change_percent"] is None
        assert result["quote_status"] == "frozen"

    def test_delayed_index_flags_its_market(self):
        from datetime import UTC, datetime

        svc = _service()
        result = svc._estimate_one(
            "159920",
            "t",
            None,
            ANCHOR,
            ("HSI.HK", 1.0),
            "index_tracking",
            "HK:HSI",
            {"HK:HSI": _Tick(0.5, "delayed")},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result["quote_status"] == "delayed"
        assert result["delayed_markets"] == ["HK"]


class TestLatestOfficial:
    def test_official_only_shape(self):
        from datetime import UTC, datetime

        svc = _service()
        result = svc._estimate_one(
            "005827",
            "t",
            None,
            ANCHOR,
            None,
            "latest_official",
            None,
            {},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result == {
            "symbol": "005827",
            "name": "t",
            "nav_official": 4.5,
            "nav_official_date": "2026-08-28",
            "nav_estimate": 4.5,
            "estimate_change_percent": None,
            "estimate_method": "latest_official",
            "coverage_percent": None,
            "holdings_report_date": None,
            "quote_status": "frozen",
            "delayed_markets": [],
            "holdings_stale": False,
            "estimate_timestamp": result["estimate_timestamp"],
        }

    def test_holdings_stale_flag(self):
        from datetime import UTC, datetime

        svc = _service()
        stale_hp = _holdings(CN_ROWS, report_date="2026-01-15", status="stale", coverage=70.0)
        assert svc._holdings_stale(stale_hp, "2026-01-15", TODAY, FRESH_DAYS) is True
        fresh_hp = _holdings(CN_ROWS)
        assert svc._holdings_stale(fresh_hp, "2026-06-30", TODAY, FRESH_DAYS) is False
        anomalous_hp = _holdings([], coverage=0.0, status="anomalous")
        assert svc._holdings_stale(anomalous_hp, None, TODAY, FRESH_DAYS) is True
        assert svc._holdings_stale(None, None, TODAY, FRESH_DAYS) is False
        # A stale tier still flows through the estimate as flagged.
        result = svc._estimate_one(
            "510300",
            "t",
            stale_hp,
            ANCHOR,
            ("000300.SS", 1.0),
            "index_tracking",
            "CN:000300",
            {"CN:000300": _Tick(1.0)},
            datetime.now(UTC),
            TODAY,
            FRESH_DAYS,
        )
        assert result["holdings_stale"] is True
