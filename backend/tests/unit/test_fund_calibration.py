"""Unit tests for fund NAV calibration (fund-intraday-nav.md §13 M3 §1.1/§1.3).

Covers compute_additive_bias (sample threshold, non-finite guards, ±50bp
clamp), apply_additive_bias, the consumption point in
FundIntradayService (bias applied to the holdings-weighted estimate +
calibrated flag + _load_calibrations mapping), compute_tracking_ratio
(regression through the origin, outlier rejection, [0.5, 1.5] clamp, sample
threshold), and FundCalibrationService.update_calibrations (row create /
update / skip paths). Conventions follow test_fund_intraday_estimator.py:
AsyncMock db, no Redis needed (nothing here touches it).
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.fund_calibration import (
    CALIBRATION_BIAS_CLAMP_PERCENT,
    SAMPLE_MIN,
    TRACKING_RATIO_MIN_SAMPLES,
    FundCalibrationService,
    apply_additive_bias,
    compute_additive_bias,
    compute_tracking_ratio,
)
from app.services.fund_intraday import FundIntradayService

TENANT = str(uuid4())
SYMBOL_ID = uuid4()
TODAY = date(2026, 8, 31)
FRESH_DAYS = 120
ANCHOR = (4.5, date(2026, 8, 28))


class _Tick:
    """Minimal stand-in for quote_batch.QuoteTick."""

    def __init__(self, change_percent: float | None, quote_status: str = "realtime"):
        self.change_percent = change_percent
        self.quote_status = quote_status


class _QueryResult:
    """Awaitable-free result stand-in supporting both row-list shapes."""

    def __init__(self, rows: list):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None


def _nav_row(method, d, nav=None, deviation=None):
    """Fake FundNAVEstimate row as _daily_samples sees it (descending order is
    guaranteed by the DB query; callers build rows accordingly)."""
    return SimpleNamespace(
        estimate_method=method,
        nav_official_date=d,
        nav_official=nav,
        nav_estimate_deviation_percent=deviation,
        estimate_timestamp=datetime(2026, 8, 28, 15, 0, tzinfo=UTC),
    )


class TestComputeAdditiveBias:
    def test_normal_samples_mean_of_official_minus_estimate(self):
        # diffs: (1.3-1.0)=0.3, (0.7-0.5)=0.2, (0.1-(-0.2))=0.3 → mean 0.8/3
        samples = [(1.0, 1.3), (0.5, 0.7), (-0.2, 0.1)]
        assert compute_additive_bias(samples) == pytest.approx(0.8 / 3)

    def test_fewer_than_sample_min_returns_none(self):
        assert compute_additive_bias([(1.0, 1.3), (0.5, 0.7)]) is None
        assert compute_additive_bias([(1.0, 1.3)]) is None
        assert compute_additive_bias([]) is None

    def test_non_numeric_samples_skipped(self):
        # Only the two valid pairs survive → below SAMPLE_MIN.
        samples = [(1.0, 1.3), (0.5, 0.7), ("bad", 0.1), (None, 0.2)]
        assert compute_additive_bias(samples) is None

    def test_nan_samples_skipped(self):
        nan = float("nan")
        # NaN pairs never contribute; with one NaN only 2 usable → None.
        assert compute_additive_bias([(1.0, 1.3), (nan, 0.7), (0.5, nan)]) is None
        # With enough finite samples the NaN pair is simply ignored.
        samples = [(1.0, 1.3), (nan, 0.7), (0.5, 0.7), (-0.2, 0.1)]
        assert compute_additive_bias(samples) == pytest.approx((0.3 + 0.2 + 0.3) / 3)

    def test_inf_samples_clamped_not_propagated(self):
        # An infinite diff would poison the mean; the final clamp is the guard
        # that keeps the stored bias finite and bounded.
        samples = [(1.0, 1.3), (0.5, 0.7), (float("inf"), 0.1)]
        assert compute_additive_bias(samples) == -CALIBRATION_BIAS_CLAMP_PERCENT

    def test_clamped_to_positive_50bp(self):
        # Each diff is +1.0 → mean 1.0 → clamped to +0.5.
        samples = [(0.0, 1.0)] * SAMPLE_MIN
        assert compute_additive_bias(samples) == CALIBRATION_BIAS_CLAMP_PERCENT

    def test_clamped_to_negative_50bp(self):
        samples = [(1.0, -1.0)] * SAMPLE_MIN
        assert compute_additive_bias(samples) == -CALIBRATION_BIAS_CLAMP_PERCENT

    def test_within_clamp_passthrough(self):
        samples = [(1.0, 1.4), (1.0, 1.4), (1.0, 1.4)]  # diff 0.4 < 0.5
        assert compute_additive_bias(samples) == pytest.approx(0.4)


class TestApplyAdditiveBias:
    def test_none_bias_returns_estimate_unchanged(self):
        assert apply_additive_bias(1.25, None) == 1.25

    def test_bias_added(self):
        assert apply_additive_bias(1.25, 0.2) == pytest.approx(1.45)
        assert apply_additive_bias(-1.0, -0.3) == pytest.approx(-1.3)

    def test_out_of_range_bias_reclamped_on_apply(self):
        # A bias that somehow bypassed the storage clamp is re-bounded.
        assert apply_additive_bias(1.0, 0.9) == pytest.approx(1.5)
        assert apply_additive_bias(1.0, -0.9) == pytest.approx(0.5)


class _Holdings:
    @staticmethod
    def payload(rows, coverage=None):
        cov = coverage if coverage is not None else round(sum(r["weight_percent"] for r in rows), 4)
        return {
            "fund_code": "510300",
            "report_date": "2026-06-30",
            "coverage_percent": cov,
            "disclosure_status": "ok",
            "holdings": rows,
        }


CN_ROWS = [
    {"stock_code": "600519", "market": "CN", "weight_percent": 40.0, "secid": "1.600519"},
    {"stock_code": "000858", "market": "CN", "weight_percent": 30.0, "secid": "0.000858"},
]
QUOTES = {"1.600519": _Tick(2.0), "0.000858": _Tick(-1.0)}  # weighted: 0.4×2 + 0.3×(-1) = 0.5


def _estimate(calibration_bias):
    svc = FundIntradayService(db=AsyncMock(), governor=AsyncMock())
    return svc._estimate_one(
        "510300",
        "沪深300ETF",
        _Holdings.payload(CN_ROWS, coverage=70.0),
        ANCHOR,
        None,
        "holdings_weighted",
        None,
        QUOTES,
        datetime.now(UTC),
        TODAY,
        FRESH_DAYS,
        calibration_bias=calibration_bias,
    )


class TestCalibrationConsumedInIntradayEstimate:
    def test_bias_applied_to_holdings_weighted_estimate(self):
        result = _estimate(calibration_bias=0.2)
        # Raw weighted change 0.5 + bias 0.2 = 0.7.
        assert result["estimate_change_percent"] == pytest.approx(0.7)
        assert result["nav_estimate"] == pytest.approx(round(4.5 * 1.007, 4))
        assert result["calibrated"] is True
        assert result["estimate_method"] == "holdings_weighted"

    def test_no_bias_leaves_estimate_uncalibrated(self):
        result = _estimate(calibration_bias=None)
        assert result["estimate_change_percent"] == pytest.approx(0.5)
        assert result["nav_estimate"] == pytest.approx(round(4.5 * 1.005, 4))
        assert result["calibrated"] is False

    def test_out_of_range_bias_bounded_at_apply(self):
        result = _estimate(calibration_bias=0.9)
        # apply_additive_bias re-clamps 0.9 → +0.5.
        assert result["estimate_change_percent"] == pytest.approx(1.0)
        assert result["calibrated"] is True

    async def test_load_calibrations_maps_codes_and_drops_null(self):
        svc = FundIntradayService(db=AsyncMock(), governor=AsyncMock())
        svc.db.execute.return_value = _QueryResult(
            [
                SimpleNamespace(fund_code="510300", additive_bias_percent=0.2),
                SimpleNamespace(fund_code="510500", additive_bias_percent=None),
            ]
        )
        assert await svc._load_calibrations(["510300", "510500"]) == {"510300": 0.2}

    async def test_load_calibrations_failure_degrades_to_empty(self):
        svc = FundIntradayService(db=AsyncMock(), governor=AsyncMock())
        svc.db.execute.side_effect = RuntimeError("db down")
        assert await svc._load_calibrations(["510300"]) == {}


class TestComputeTrackingRatio:
    def test_ols_through_origin_exact(self):
        # fund = 0.9 × index on every sample → ratio exactly 0.9.
        samples = [(0.9 * i, i) for i in (1.0, 2.0, -1.0, 0.5, 3.0)]
        assert compute_tracking_ratio(samples) == pytest.approx(0.9)

    def test_outlier_rejected_before_fit(self):
        # Five ~1:1 days plus one anomalous 50:1 day. Raw OLS would land at
        # 55/6 ≈ 9.17 (clamped 1.5); the robust median screens the outlier,
        # so the fit stays at the true 1.0.
        samples = [(1.0, 1.0), (1.02, 1.0), (0.98, 1.0), (1.01, 1.0), (0.99, 1.0), (50.0, 1.0)]
        assert compute_tracking_ratio(samples) == pytest.approx(1.0)

    def test_outlier_kept_when_too_few_survive(self):
        # Removing the outlier would leave 4 < TRACKING_RATIO_MIN_SAMPLES, so
        # every usable sample is kept and the raw OLS (clamped) is used.
        samples = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (30.0, 1.0)]
        assert compute_tracking_ratio(samples) == pytest.approx(1.5)

    def test_clamped_upper_and_lower(self):
        assert compute_tracking_ratio([(2.0 * i, i) for i in (1.0, 2.0, 1.5, -1.0, 3.0)]) == 1.5
        assert compute_tracking_ratio([(0.1 * i, i) for i in (1.0, 2.0, 1.5, -1.0, 3.0)]) == 0.5

    def test_fewer_than_min_samples_returns_none(self):
        samples = [(0.9 * i, i) for i in (1.0, 2.0, -1.0, 0.5)]  # 4 < 5
        assert compute_tracking_ratio(samples) is None

    def test_all_zero_index_changes_returns_none(self):
        # No per-sample ratio can be formed and the OLS denominator is 0.
        assert compute_tracking_ratio([(0.1, 0.0)] * TRACKING_RATIO_MIN_SAMPLES) is None

    def test_nan_and_non_numeric_samples_skipped(self):
        nan = float("nan")
        base = [(0.9 * i, i) for i in (1.0, 2.0, -1.0, 0.5, 3.0)]
        assert compute_tracking_ratio(base + [(nan, 1.0), ("bad", 2.0)]) == pytest.approx(0.9)
        assert compute_tracking_ratio([(0.9, 1.0), (nan, 1.0), (1.8, nan)]) is None


class TestUpdateCalibrations:
    """AsyncMock db driving the execute() sequence: fund symbols → NAV rows →
    existing calibration row lookup."""

    def _service(self, results: list):
        db = AsyncMock()
        db.execute.side_effect = results
        # session.add is synchronous in SQLAlchemy; AsyncMock would make it a
        # never-awaited coroutine.
        db.add = MagicMock()
        return FundCalibrationService(db), db

    def _fund(self, symbol="510300"):
        return SimpleNamespace(id=SYMBOL_ID, symbol=symbol, tenant_id=TENANT)

    # Official NAVs 2.0/2.02/2.04/2.06 (day-over-day changes compound) vs
    # closing estimate deviations 0.7/0.8/0.9 on dates d2/d3/d4.
    _OFFICIAL_NAVS = (2.0, 2.02, 2.04, 2.06)
    _ESTIMATE_DEVIATIONS = (0.7, 0.8, 0.9)

    @classmethod
    def _expected_bias(cls) -> float:
        changes = [
            (cur - prev) / prev * 100 for prev, cur in zip(cls._OFFICIAL_NAVS, cls._OFFICIAL_NAVS[1:], strict=False)
        ]
        diffs = [o - e for o, e in zip(changes, cls._ESTIMATE_DEVIATIONS, strict=True)]
        return sum(diffs) / len(diffs)

    def _daily_rows(self):
        """Descending rows producing 3 samples (see _OFFICIAL_NAVS /
        _ESTIMATE_DEVIATIONS); each official date also carries a closing
        estimate row so the pairing is realistic."""
        d1, d2, d3, d4 = (date(2026, 8, n) for n in (25, 26, 27, 28))
        return [
            _nav_row("official", d4, nav=self._OFFICIAL_NAVS[3]),
            _nav_row("holdings_weighted", d4, nav=self._OFFICIAL_NAVS[3], deviation=self._ESTIMATE_DEVIATIONS[2]),
            _nav_row("official", d3, nav=self._OFFICIAL_NAVS[2]),
            _nav_row("holdings_weighted", d3, nav=self._OFFICIAL_NAVS[2], deviation=self._ESTIMATE_DEVIATIONS[1]),
            _nav_row("official", d2, nav=self._OFFICIAL_NAVS[1]),
            _nav_row("index_tracking", d2, nav=self._OFFICIAL_NAVS[1], deviation=self._ESTIMATE_DEVIATIONS[0]),
            _nav_row("official", d1, nav=self._OFFICIAL_NAVS[0]),
        ]

    async def test_creates_calibration_row(self):
        svc, db = self._service([_QueryResult([self._fund()]), _QueryResult(self._daily_rows()), _QueryResult([])])
        updated = await svc.update_calibrations(TENANT)
        assert updated == 1
        db.add.assert_called_once()
        row = db.add.call_args.args[0]
        assert row.fund_code == "510300"
        assert row.tenant_id == TENANT
        assert row.symbol_id == SYMBOL_ID
        assert row.additive_bias_percent == pytest.approx(self._expected_bias())
        assert row.sample_count == 3
        db.commit.assert_awaited_once()

    async def test_updates_existing_row(self):
        existing = SimpleNamespace(
            tenant_id=TENANT,
            fund_code="510300",
            symbol_id=SYMBOL_ID,
            additive_bias_percent=0.05,
            sample_count=3,
            updated_at=datetime(2026, 8, 30, 20, 0, tzinfo=UTC),
        )
        svc, db = self._service(
            [_QueryResult([self._fund()]), _QueryResult(self._daily_rows()), _QueryResult([existing])]
        )
        updated = await svc.update_calibrations(TENANT)
        assert updated == 1
        db.add.assert_not_called()
        assert existing.additive_bias_percent == pytest.approx(self._expected_bias())
        assert existing.sample_count == 3
        db.commit.assert_awaited_once()

    async def test_insufficient_samples_skipped(self):
        # Only two official dates → at most one pair → below SAMPLE_MIN.
        rows = [
            _nav_row("official", date(2026, 8, 28), nav=2.02),
            _nav_row("holdings_weighted", date(2026, 8, 28), nav=2.02, deviation=0.9),
            _nav_row("official", date(2026, 8, 27), nav=2.0),
        ]
        svc, db = self._service([_QueryResult([self._fund()]), _QueryResult(rows)])
        assert await svc.update_calibrations(TENANT) == 0
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_fund_without_nav_rows_is_skipped(self):
        svc, db = self._service([_QueryResult([self._fund()]), _QueryResult([])])
        assert await svc.update_calibrations(TENANT) == 0
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    async def test_zero_nav_anchor_pair_dropped(self):
        # A zero/None official NAV cannot anchor a daily change (division
        # guard): the pair is dropped, leaving <SAMPLE_MIN samples.
        d1, d2, d3, d4 = (date(2026, 8, n) for n in (25, 26, 27, 28))
        rows = [
            _nav_row("official", d4, nav=2.06),
            _nav_row("holdings_weighted", d4, nav=2.06, deviation=0.9),
            _nav_row("official", d3, nav=0),  # zero anchor → pair dropped
            _nav_row("holdings_weighted", d3, nav=0, deviation=0.8),
            _nav_row("official", d2, nav=None),  # None anchor → pair dropped
            _nav_row("official", d1, nav=2.0),
        ]
        svc, db = self._service([_QueryResult([self._fund()]), _QueryResult(rows)])
        assert await svc.update_calibrations(TENANT) == 0
        db.add.assert_not_called()

    async def test_no_fund_symbols_returns_zero(self):
        svc, db = self._service([_QueryResult([])])
        assert await svc.update_calibrations(TENANT) == 0
        db.commit.assert_not_awaited()
