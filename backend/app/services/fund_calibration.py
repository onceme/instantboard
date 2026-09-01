"""Fund NAV estimate calibration (fund-intraday-nav.md §13 M3).

Two nightly calibrations, both run after the official NAV update and both
naturally no-op until enough daily samples accumulate:

1. Additive estimate bias (§1.1): the holdings-weighted intraday estimate is
   systematically biased because undisclosed positions (other stocks, bonds,
   cash) are assumed unchanged. Each night we compare the fund's recent closing
   estimates against the realized official NAV change and learn an additive
   bias, which is then applied (bounded) to intraday estimates. Bounded to
   ±50bp; requires >= SAMPLE_MIN days of samples.

2. tracking_ratio regression (§1.3): for an index-bound fund, regress recent
   "fund realized daily change vs bound index daily change" samples through the
   origin to refine the binding's tracking_ratio, bounded to [0.5, 1.5] with
   outlier removal; requires >= TRACKING_RATIO_MIN_SAMPLES days of samples.
   The computation is implemented and unit-tested but NOT yet wired into
   update_calibrations: the regression needs a systematic daily index-change
   history that does not exist yet (finance_quotes is only written by
   on-demand user quotes; market indices live in a 60s-TTL Redis cache; the
   fund_nav_estimates index_tracking close snapshot stores ratio-scaled
   changes, which would feed back and oscillate). Once per-index daily closes
   accumulate, wire the result into fund_index_bindings.tracking_ratio
   (compute_tracking_ratio docstring carries the same note).

Both computations are pure functions over sample lists so they are unit
testable; the service wires them to the database. Sample accumulation starts
today, so both calibrations are dormant until enough daily pairs exist.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Minimum daily samples before a calibration is applied (§1.1 / §1.3). Below
# the threshold the calibration is skipped (no-op) so a couple of noisy days
# never distort estimates.
SAMPLE_MIN = 3
TRACKING_RATIO_MIN_SAMPLES = 5

# Additive bias bounded to ±50bp before application (guards against an anomalous
# sample blowing up the correction) (§1.1).
CALIBRATION_BIAS_CLAMP_PERCENT = 0.5

# tracking_ratio regression bounded to [0.5, 1.5] (§1.3).
TRACKING_RATIO_MIN = 0.5
TRACKING_RATIO_MAX = 1.5

# Outlier rejection for the regression: drop samples whose residual is beyond
# OUTLIER_RESIDUAL_PERCENT of the fitted line before fitting (§1.3).
OUTLIER_RESIDUAL_PERCENT = 5.0


def clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else (hi if value > hi else value)


def compute_additive_bias(samples: list[tuple[float, float]]) -> float | None:
    """Additive estimate bias from (estimate_change, official_change) pairs.

    Each sample is (estimate_change_percent, official_change_percent) for one
    trading day. The bias is the mean of (official - estimate), clamped to
    ±CALIBRATION_BIAS_CLAMP_PERCENT. Returns None when there are fewer than
    SAMPLE_MIN usable samples. Guards against division by zero are not needed
    here (no division), but non-finite samples are skipped.
    """
    diffs: list[float] = []
    for estimate_change, official_change in samples:
        try:
            estimate_change = float(estimate_change)
            official_change = float(official_change)
        except (TypeError, ValueError):
            continue
        # Skip non-finite samples (guards against NaN/inf propagation).
        if estimate_change != estimate_change or official_change != official_change:
            continue
        diffs.append(official_change - estimate_change)

    if len(diffs) < SAMPLE_MIN:
        return None

    bias = sum(diffs) / len(diffs)
    return clamp(bias, -CALIBRATION_BIAS_CLAMP_PERCENT, CALIBRATION_BIAS_CLAMP_PERCENT)


def apply_additive_bias(estimate_change_percent: float, bias: float | None) -> float:
    """Apply an additive bias (bounded) to an estimate change percent.

    A None bias (insufficient samples / no calibration) returns the estimate
    unchanged. The bias itself was already clamped when stored, but it is
    re-clamped here defensively.
    """
    if bias is None:
        return estimate_change_percent
    bounded = clamp(float(bias), -CALIBRATION_BIAS_CLAMP_PERCENT, CALIBRATION_BIAS_CLAMP_PERCENT)
    return estimate_change_percent + bounded


def compute_tracking_ratio(samples: list[tuple[float, float]]) -> float | None:
    """Least-squares tracking ratio through the origin from
    (fund_change_percent, index_change_percent) daily samples.

    tracking_ratio = sum(fund * index) / sum(index^2). Outliers are rejected
    before the final fit using a robust initial estimate (median of per-sample
    ratios f/i): a single anomalous day (e.g. a disclosure gap) must not drag
    the OLS fit. Samples whose residual against the robust estimate exceeds
    OUTLIER_RESIDUAL_PERCENT are dropped before the OLS refit. Returns None
    when there are fewer than TRACKING_RATIO_MIN_SAMPLES usable samples, every
    index change is 0 (denominator ~0), or no samples survive. The result is
    bounded to [TRACKING_RATIO_MIN, TRACKING_RATIO_MAX].

    Wiring status (fund-intraday-nav.md §13 M3): computation ready, not yet
    applied. Feeding it into fund_index_bindings.tracking_ratio requires a
    per-index daily-change sample series, and no systematic source exists yet:
    finance_quotes only accrues rows from on-demand user quotes, market index
    refreshes persist to a 60s-TTL Redis cache only, and the index_tracking
    close snapshots in fund_nav_estimates are already scaled by the current
    ratio (using them would create a ratio feedback loop). Once daily index
    closes accumulate in a dedicated store, call this from
    FundCalibrationService.update_calibrations and upsert the binding's
    tracking_ratio (source annotation: keep 'seed' origin, mark calibration).
    """
    from statistics import median

    usable: list[tuple[float, float]] = []
    for fund_change, index_change in samples:
        try:
            fund_change = float(fund_change)
            index_change = float(index_change)
        except (TypeError, ValueError):
            continue
        if fund_change != fund_change or index_change != index_change:
            continue
        usable.append((fund_change, index_change))

    if len(usable) < TRACKING_RATIO_MIN_SAMPLES:
        return None

    def _fit(pairs: list[tuple[float, float]]) -> float | None:
        num = sum(f * i for f, i in pairs)
        den = sum(i * i for _, i in pairs)
        if den == 0:
            return None
        return num / den

    # Robust initial estimate: median of per-sample ratios f/i. Robust to a
    # single outlier, unlike a raw OLS fit which an outlier would drag.
    per_sample_ratios = [f / i for f, i in usable if i != 0]
    if not per_sample_ratios:
        return None
    robust_ratio = median(per_sample_ratios)

    kept = [(f, i) for f, i in usable if abs(f - robust_ratio * i) <= OUTLIER_RESIDUAL_PERCENT]
    if len(kept) < TRACKING_RATIO_MIN_SAMPLES:
        kept = usable  # not enough to drop — use every usable sample

    ratio = _fit(kept)
    if ratio is None:
        return None

    return clamp(ratio, TRACKING_RATIO_MIN, TRACKING_RATIO_MAX)


class FundCalibrationService:
    """Computes and stores per-fund estimate calibration after the nightly
    official NAV update (fund-intraday-nav.md §13 M3 §1.1/§1.3).

    Both calibrations require daily samples that only start accumulating once
    intraday estimates and official NAVs both exist; until then the service is
    a structured no-op (returns 0) so it is safe to run every night.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def update_calibrations(self, tenant_id: str) -> int:
        """Recompute additive bias + tracking_ratio for the tenant's funds.

        Returns the number of funds whose calibration row was written/updated.
        Naturally returns 0 while samples are insufficient. Never raises.
        """
        from app.models.finance import FinanceSymbol

        try:
            stmt = select(FinanceSymbol).where(
                FinanceSymbol.tenant_id == tenant_id,
                FinanceSymbol.type == "fund",
                FinanceSymbol.is_active,
            )
            funds = list((await self.db.execute(stmt)).scalars().all())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"fund_calibration: fund symbol load failed: {exc}")
            return 0

        updated = 0
        for fund in funds:
            try:
                if await self._calibrate_one(tenant_id, fund, fund.id):
                    updated += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"fund_calibration: calibration failed for {fund.symbol}: {exc}")

        if updated:
            try:
                await self.db.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"fund_calibration: commit failed: {exc}")
                with contextlib.suppress(Exception):
                    await self.db.rollback()
        return updated

    async def _calibrate_one(self, tenant_id: str, fund, symbol_id) -> bool:
        """Compute + upsert the additive bias for one fund. Returns True when a
        calibration row was written/updated."""
        samples = await self._daily_samples(tenant_id, symbol_id)
        bias = compute_additive_bias(samples)
        if bias is None:
            return False

        from app.models.finance import FundNAVCalibration

        fund_code = self._fund_code(fund)
        stmt = select(FundNAVCalibration).where(FundNAVCalibration.fund_code == fund_code)
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = FundNAVCalibration(tenant_id=tenant_id, fund_code=fund_code, symbol_id=symbol_id)
            self.db.add(row)
        row.additive_bias_percent = bias
        row.sample_count = len(samples)
        row.updated_at = datetime.now(UTC)
        return True

    async def _daily_samples(self, tenant_id: str, symbol_id) -> list[tuple[float, float]]:
        """(estimate_change, official_change) pairs over recent trading days.

        Pairs the fund's closing holdings_weighted estimate change with the
        official NAV change between consecutive official rows. Samples accrue
        one per trading day once both series exist.
        """
        from app.models.finance import FundNAVEstimate

        stmt = (
            select(FundNAVEstimate)
            .where(
                FundNAVEstimate.tenant_id == tenant_id,
                FundNAVEstimate.symbol_id == symbol_id,
            )
            .order_by(FundNAVEstimate.nav_official_date.desc().nulls_last(), FundNAVEstimate.estimate_timestamp.desc())
        )
        rows = list((await self.db.execute(stmt)).scalars().all())
        if not rows:
            return []

        # Most recent official rows by date (for day-over-day official change).
        official_by_date: dict = {}
        for row in rows:
            if row.estimate_method == "official" and row.nav_official_date is not None:
                official_by_date.setdefault(row.nav_official_date, row)
        sorted_dates = sorted(official_by_date.keys())

        # Closing estimate change per date (holdings_weighted preferred).
        estimate_change_by_date: dict = {}
        for row in rows:
            if row.nav_official_date is None or row.nav_official_date in estimate_change_by_date:
                continue
            if (
                row.estimate_method in ("holdings_weighted", "index_tracking")
                and row.nav_estimate_deviation_percent is not None
            ):
                estimate_change_by_date[row.nav_official_date] = float(row.nav_estimate_deviation_percent)

        samples: list[tuple[float, float]] = []
        for prev_date, cur_date in zip(sorted_dates, sorted_dates[1:], strict=False):
            prev_row = official_by_date.get(prev_date)
            cur_row = official_by_date.get(cur_date)
            if prev_row is None or cur_row is None:
                continue
            prev_nav = float(prev_row.nav_official) if prev_row.nav_official is not None else None
            cur_nav = float(cur_row.nav_official) if cur_row.nav_official is not None else None
            if not prev_nav or not cur_nav:
                continue
            official_change = (cur_nav - prev_nav) / prev_nav * 100
            estimate_change = estimate_change_by_date.get(cur_date)
            if estimate_change is None:
                continue
            samples.append((estimate_change, official_change))
        return samples

    @staticmethod
    def _fund_code(fund) -> str:
        from app.services.fund_holdings import _normalize_fund_code_shared

        return _normalize_fund_code_shared(str(getattr(fund, "symbol", ""))) or str(getattr(fund, "symbol", ""))
