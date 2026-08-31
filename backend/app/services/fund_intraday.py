"""Fund intraday NAV estimate cycle (fund-intraday-nav.md §4, §7.2, §8, §3.4).

One call computes the full estimate array for the cross-tenant union of
followed funds: followed-codes union (cached) → holdings/meta/anchor/binding
loads → constituent quote batch through the budget-governed upstream chains →
per-fund tiered decision (holdings_weighted → index_tracking → latest_official)
→ Redis realtime cache → tenant-filtered SSE fan-out with 1bp change detection
→ downsampled DB flush. Redis realtime values and the SSE push are the live
surface; fund_nav_estimates writes are throttled to ≤1/min/fund plus forced
state-switch writes (§3.4).

Background callers must open the session with apply_service_context (RLS
bypass): the cycle reads watchlist/symbol/NAV rows across all tenants and
writes system-tenant rows.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys, get_redis_client
from app.core.sse_router import SSEEventType, event_router
from app.models.finance import (
    DISCLOSURE_STATUS_ANOMALOUS,
    DISCLOSURE_STATUS_OK,
    FinanceSymbol,
    FundIndexBinding,
    FundNAVEstimate,
)
from app.services.fund_holdings import FundHoldingsService, _normalize_fund_code_shared
from app.services.quote_batch import Constituent, fetch_quotes
from app.services.upstream_budget import get_budget_governor

logger = logging.getLogger(__name__)

# fund_nav_estimates.estimate_method row family written by the daily official
# job; kept as a local constant so this module never imports services/finance.py
# (which imports back into this package via lazy hooks).
ESTIMATE_METHOD_OFFICIAL = "official"

# Cross-cycle change-detection / flush state (process-wide; the cycle job is a
# single scheduler-owned loop, so module state is the natural owner). Tests
# reset via reset_cycle_state().
_LAST_PUSH_SIGNATURES: dict[str, tuple] = {}
_LAST_FLUSH_MODES: dict[str, tuple] = {}
# Last successful cycle context (fund-intraday-nav.md §3.4 case 3, M2): the
# open→closed gate edge writes the closing snapshot from the LAST computed
# estimates — recomputing at the edge is not an option (market already closed,
# quotes stale). Keys: results_by_code, ids_per_code.
_LAST_CYCLE_CONTEXT: dict = {}


def reset_cycle_state() -> None:
    _LAST_PUSH_SIGNATURES.clear()
    _LAST_FLUSH_MODES.clear()
    _LAST_CYCLE_CONTEXT.clear()


def _index_symbol_to_constituent(index_symbol: str) -> tuple[str, str]:
    """Yahoo-style index symbol → (stock_code, market) for the quote batch.
    All seeded bindings are CN indices; unknown shapes default to CN."""
    sym = (index_symbol or "").strip().upper()
    for suffix, market in ((".SS", "CN"), (".SH", "CN"), (".SZ", "CN"), (".BJ", "CN"), (".HK", "HK")):
        if sym.endswith(suffix):
            return sym[: -len(suffix)], market
    return sym, "CN"


class FundIntradayService:
    def __init__(self, db: AsyncSession, governor=None):
        self.db = db
        self.governor = governor or get_budget_governor()
        self._fund_holdings: FundHoldingsService | None = None

    @property
    def fund_holdings(self) -> FundHoldingsService:
        if self._fund_holdings is None:
            self._fund_holdings = FundHoldingsService(db=self.db, governor=self.governor)
        return self._fund_holdings

    # ------------------------------------------------------------------
    # Step 3/4/5: followed union, truncation, reference loads
    # ------------------------------------------------------------------

    async def _load_followed(self) -> tuple[list[str], dict[str, set[str]]]:
        """Followed-union codes (truncated, deterministic) + per-tenant maps."""
        union_codes, per_tenant = await self.fund_holdings.get_followed_codes()
        if not union_codes:
            return [], per_tenant
        max_funds = settings.fund_nav_intraday_max_funds
        if len(union_codes) > max_funds:
            ordered = await self._codes_by_earliest_follow(set(union_codes))
            logger.warning(
                f"fund_intraday: followed union {len(union_codes)} exceeds cap {max_funds}, "
                f"truncating to the earliest-followed funds"
            )
            union_codes = ordered[:max_funds]
        return union_codes, per_tenant

    async def _codes_by_earliest_follow(self, code_candidates: set[str]) -> list[str]:
        """Deterministic truncation order: earliest-followed ascending
        (§7.2 step 5). Codes without a resolvable follow time sort last."""
        from app.models.watchlist import WatchlistItem

        stmt = (
            select(FinanceSymbol.symbol, func.min(WatchlistItem.created_at))
            .join(FinanceSymbol, WatchlistItem.symbol_id == FinanceSymbol.id)
            .where(FinanceSymbol.type == "fund", FinanceSymbol.is_active)
            .group_by(FinanceSymbol.symbol)
        )
        result = await self.db.execute(stmt)
        entries: list[tuple[float, str]] = []
        for symbol, first_followed in result.all():
            code = _normalize_fund_code_shared(str(symbol or ""))
            if code in code_candidates:
                when = first_followed.timestamp() if first_followed is not None else float("inf")
                entries.append((when, code))
        entries.sort()
        seen = {code for _, code in entries}
        for code in sorted(code_candidates - seen):
            entries.append((float("inf"), code))
        return [code for _, code in entries]

    async def _load_symbol_map(self, code_set: set[str]) -> tuple[dict[str, list], dict[str, str]]:
        """code → [finance_symbols ids across tenants] and code → display name."""
        stmt = select(FinanceSymbol).where(FinanceSymbol.type == "fund", FinanceSymbol.is_active)
        result = await self.db.execute(stmt)
        ids_per_code: dict[str, list] = {}
        name_per_code: dict[str, str] = {}
        for sym in result.scalars().all():
            code = _normalize_fund_code_shared(str(sym.symbol or ""))
            if code is None or code not in code_set:
                continue
            ids_per_code.setdefault(code, []).append(sym.id)
            if code not in name_per_code or sym.tenant_id == SYSTEM_TENANT_ID:
                name_per_code[code] = sym.name
        return ids_per_code, name_per_code

    async def _load_official_anchors(self, ids_per_code: dict[str, list]) -> dict[str, tuple[float, date | None]]:
        """Latest official NAV per code — mirrors get_fund_nav's read order
        (nav_official_date DESC NULLS LAST, then estimate_timestamp DESC),
        batched across all codes' symbol ids."""
        all_ids = [sid for ids in ids_per_code.values() for sid in ids]
        if not all_ids:
            return {}
        id_to_code = {sid: code for code, ids in ids_per_code.items() for sid in ids}
        stmt = (
            select(FundNAVEstimate)
            .where(
                FundNAVEstimate.symbol_id.in_(all_ids),
                FundNAVEstimate.estimate_method == ESTIMATE_METHOD_OFFICIAL,
                FundNAVEstimate.nav_official.is_not(None),
            )
            .order_by(
                FundNAVEstimate.nav_official_date.desc().nulls_last(),
                FundNAVEstimate.estimate_timestamp.desc(),
            )
        )
        result = await self.db.execute(stmt)
        anchors: dict[str, tuple[float, date | None]] = {}
        for row in result.scalars().all():
            code = id_to_code.get(row.symbol_id)
            if code is not None and code not in anchors:
                anchors[code] = (float(row.nav_official), row.nav_official_date)
        return anchors

    async def _load_bindings(self, codes: list[str], ids_per_code: dict[str, list]) -> dict[str, tuple[str, float]]:
        """Binding read order (§3.3): fund_index_bindings, then residual
        underlying_index_symbol rows for legacy estimates."""
        stmt = select(FundIndexBinding).where(FundIndexBinding.fund_code.in_(codes))
        result = await self.db.execute(stmt)
        bindings: dict[str, tuple[str, float]] = {
            row.fund_code: (str(row.index_symbol), float(row.tracking_ratio or 1.0)) for row in result.scalars().all()
        }

        missing_ids = [sid for code in codes if code not in bindings for sid in ids_per_code.get(code, [])]
        if missing_ids:
            id_to_code = {sid: code for code in codes if code not in bindings for sid in ids_per_code.get(code, [])}
            legacy_stmt = (
                select(FundNAVEstimate)
                .where(
                    FundNAVEstimate.symbol_id.in_(missing_ids),
                    FundNAVEstimate.underlying_index_symbol.is_not(None),
                )
                .order_by(FundNAVEstimate.estimate_timestamp.desc())
            )
            legacy_result = await self.db.execute(legacy_stmt)
            for row in legacy_result.scalars().all():
                code = id_to_code.get(row.symbol_id)
                if code is not None and code not in bindings:
                    bindings[code] = (str(row.underlying_index_symbol), 1.0)
        return bindings

    # ------------------------------------------------------------------
    # Tier planning (§4.3)
    # ------------------------------------------------------------------

    def _plan_tier(
        self,
        holdings_payload: dict | None,
        binding: tuple[str, float] | None,
        fresh_days: int,
        min_coverage: float,
        today: date,
    ) -> str:
        if holdings_payload and holdings_payload.get("holdings") and holdings_payload.get("report_date"):
            if holdings_payload.get("disclosure_status") == DISCLOSURE_STATUS_ANOMALOUS:
                return "index_tracking" if binding is not None else "latest_official"
            try:
                report_date = date.fromisoformat(str(holdings_payload["report_date"]))
            except ValueError:
                report_date = None
            if report_date is not None:
                age_days = (today - report_date).days
                coverage = holdings_payload.get("coverage_percent")
                if (
                    age_days <= fresh_days
                    and coverage is not None
                    and float(coverage) >= min_coverage
                    and holdings_payload.get("disclosure_status") == DISCLOSURE_STATUS_OK
                ):
                    return "holdings_weighted"
        if binding is not None:
            return "index_tracking"
        return "latest_official"

    # ------------------------------------------------------------------
    # Main cycle (§7.2 steps 3-11; the CN gate lives in the scheduler)
    # ------------------------------------------------------------------

    async def compute_cycle(self) -> dict:
        now = datetime.now(UTC)
        today = now.date()

        codes, per_tenant = await self._load_followed()
        if not codes:
            return {"codes": 0, "results": [], "pushed_tenants": 0}

        code_set = set(codes)
        ids_per_code, name_per_code = await self._load_symbol_map(code_set)
        anchors = await self._load_official_anchors(ids_per_code)
        bindings = await self._load_bindings(codes, ids_per_code)

        holdings_by_code: dict[str, dict | None] = {}
        for code in codes:
            try:
                holdings_by_code[code] = await self.fund_holdings.get_holdings(code)
            except Exception as exc:  # noqa: BLE001 - one fund's load failure degrades that fund only
                logger.warning(f"fund_intraday: holdings load failed for {code}: {exc}")
                holdings_by_code[code] = None

        fresh_days = settings.fund_nav_holdings_fresh_days
        min_coverage = settings.fund_nav_holdings_min_coverage

        tier_plan: dict[str, str] = {}
        constituents: dict[str, Constituent] = {}
        index_keys: dict[str, str] = {}
        for code in codes:
            hp = holdings_by_code.get(code)
            tier = self._plan_tier(hp, bindings.get(code), fresh_days, min_coverage, today)
            tier_plan[code] = tier
            if tier == "holdings_weighted" and hp:
                for holding in hp.get("holdings", []):
                    market = str(holding.get("market") or "")
                    stock_code = str(holding.get("stock_code") or "")
                    if not market or not stock_code:
                        continue
                    key = holding.get("secid") or f"{market}:{stock_code}"
                    if key not in constituents:
                        constituents[key] = Constituent(
                            key=key, stock_code=stock_code, market=market, secid=holding.get("secid")
                        )
            elif tier == "index_tracking":
                index_symbol, _ratio = bindings[code]
                stock_code, market = _index_symbol_to_constituent(index_symbol)
                key = f"{market}:{stock_code}"
                index_keys[code] = key
                if key not in constituents:
                    constituents[key] = Constituent(key=key, stock_code=stock_code, market=market, secid=None)

        if constituents:
            quotes, missing = await fetch_quotes(list(constituents.values()), self.governor)
            if missing:
                logger.info(f"fund_intraday: {len(missing)} constituent quotes unresolved (degraded)")
        else:
            quotes, missing = {}, []

        results: list[dict] = []
        for code in codes:
            results.append(
                self._estimate_one(
                    code=code,
                    name=name_per_code.get(code, code),
                    holdings_payload=holdings_by_code.get(code),
                    anchor=anchors.get(code),
                    binding=bindings.get(code),
                    tier=tier_plan[code],
                    index_key=index_keys.get(code),
                    quotes=quotes,
                    now=now,
                    today=today,
                    fresh_days=fresh_days,
                )
            )

        # Stash the cycle context for the open→closed gate-edge closing snapshot
        # write (§3.4 case 3, M2) — the scheduler calls write_close_snapshots()
        # with the same session when the CN market flips closed.
        _LAST_CYCLE_CONTEXT.clear()
        _LAST_CYCLE_CONTEXT.update(
            {
                "results_by_code": {r["symbol"]: r for r in results},
                "ids_per_code": ids_per_code,
            }
        )

        await self._write_rt_cache(results)
        pushed_tenants = await self._push_batch(results, per_tenant)
        await self._flush_results(results, ids_per_code)

        # Structured observability (fund-intraday-nav.md §13 M2): a cycle that
        # nears the interval budget risks coalescing/overlap, so it warns early.
        elapsed = (datetime.now(UTC) - now).total_seconds()
        if elapsed > 2.5:
            logger.warning(
                f"fund_intraday: cycle duration {elapsed:.2f}s exceeds the 2.5s "
                f"soft budget (codes={len(codes)}, pushed_tenants={pushed_tenants})"
            )

        return {"codes": len(codes), "results": results, "pushed_tenants": pushed_tenants}

    # ------------------------------------------------------------------
    # Per-fund estimate (§4.1 / §4.2 / §4.3)
    # ------------------------------------------------------------------

    def _estimate_one(
        self,
        code: str,
        name: str,
        holdings_payload: dict | None,
        anchor: tuple[float, date | None] | None,
        binding: tuple[str, float] | None,
        tier: str,
        index_key: str | None,
        quotes: dict,
        now: datetime,
        today: date,
        fresh_days: int,
    ) -> dict:
        nav_official = anchor[0] if anchor else None
        nav_official_date = str(anchor[1]) if anchor and anchor[1] else None
        report_date_iso = holdings_payload.get("report_date") if holdings_payload else None
        holdings_stale = self._holdings_stale(holdings_payload, report_date_iso, today, fresh_days)

        base = {
            "symbol": code,
            "name": name,
            "nav_official": nav_official,
            "nav_official_date": nav_official_date,
            "holdings_report_date": report_date_iso,
            "holdings_stale": holdings_stale,
            "estimate_timestamp": now.isoformat(),
        }

        if tier == "holdings_weighted":
            estimated = self._estimate_holdings_weighted(holdings_payload, quotes, base)
            if estimated is not None:
                return estimated
            tier = "index_tracking" if binding is not None else "latest_official"

        if tier == "index_tracking":
            return self._estimate_index_tracking(binding, index_key, quotes, base)

        return self._estimate_latest_official(base)

    @staticmethod
    def _holdings_stale(
        holdings_payload: dict | None, report_date_iso: str | None, today: date, fresh_days: int
    ) -> bool:
        if holdings_payload and holdings_payload.get("disclosure_status") == DISCLOSURE_STATUS_ANOMALOUS:
            return True
        if not report_date_iso:
            return False
        try:
            report_date = date.fromisoformat(str(report_date_iso))
        except ValueError:
            return False
        return (today - report_date).days > fresh_days

    @staticmethod
    def _estimate_holdings_weighted(holdings_payload: dict | None, quotes: dict, base: dict) -> dict | None:
        """Σᵢ (wᵢ/100 × chgᵢ); undiscussed positions contribute 0 by design
        (§4.1). Returns None when no constituent quote resolved (caller then
        degrades one tier)."""
        weighted_change = 0.0
        resolved = 0
        coverage = 0.0
        realtime_markets: set[str] = set()
        delayed_markets: set[str] = set()

        for holding in (holdings_payload or {}).get("holdings", []):
            weight = float(holding.get("weight_percent") or 0.0)
            coverage += weight
            market = str(holding.get("market") or "")
            key = holding.get("secid") or f"{market}:{holding.get('stock_code')}"
            tick = quotes.get(key)
            if tick is not None and tick.change_percent is not None:
                weighted_change += (weight / 100.0) * tick.change_percent
                resolved += 1
                if tick.quote_status == "delayed":
                    delayed_markets.add(market)
                else:
                    realtime_markets.add(market)
            elif market:
                # Missing quote: the contribution stays 0 (unknown-position
                # treatment) and the leg is conservatively displayed as delayed.
                delayed_markets.add(market)

        if resolved == 0:
            return None

        if delayed_markets and realtime_markets:
            quote_status = "mixed"
        elif delayed_markets:
            quote_status = "delayed"
        else:
            quote_status = "realtime"

        change_percent = round(weighted_change, 4)
        nav_official = base.get("nav_official")
        result = dict(base)
        result.update(
            {
                "nav_estimate": round(nav_official * (1 + change_percent / 100), 4) if nav_official else None,
                "estimate_change_percent": change_percent,
                "estimate_method": "holdings_weighted",
                "coverage_percent": min(round(coverage, 4), 100.0),
                "quote_status": quote_status,
                "delayed_markets": sorted(m for m in delayed_markets if m),
            }
        )
        return result

    def _estimate_index_tracking(
        self, binding: tuple[str, float] | None, index_key: str | None, quotes: dict, base: dict
    ) -> dict:
        result = dict(base)
        tick = quotes.get(index_key) if index_key else None
        if binding is None or tick is None or tick.change_percent is None:
            return self._estimate_latest_official(base)

        index_symbol, ratio = binding
        change_percent = round(tick.change_percent * ratio, 4)
        nav_official = base.get("nav_official")
        result.update(
            {
                "nav_estimate": round(nav_official * (1 + change_percent / 100), 4) if nav_official else None,
                "estimate_change_percent": change_percent,
                "estimate_method": "index_tracking",
                "coverage_percent": None,
                "quote_status": tick.quote_status,
                "delayed_markets": [index_key.split(":")[0]] if tick.quote_status == "delayed" and index_key else [],
            }
        )
        return result

    @staticmethod
    def _estimate_latest_official(base: dict) -> dict:
        result = dict(base)
        result.update(
            {
                "nav_estimate": base.get("nav_official"),
                "estimate_change_percent": None,
                "estimate_method": "latest_official",
                "coverage_percent": None,
                "quote_status": "frozen",
                "delayed_markets": [],
            }
        )
        return result

    # ------------------------------------------------------------------
    # Redis realtime cache (§3.4)
    # ------------------------------------------------------------------

    async def _write_rt_cache(self, results: list[dict]) -> None:
        if not results:
            return
        try:
            client = await get_redis_client()
            pipe = client.pipeline(transaction=False)
            for r in results:
                pipe.set(RedisKeys.fund_nav_rt_key(r["symbol"]), json.dumps(r), ex=RedisKeys.FUND_NAV_RT_TTL)
            await pipe.execute()
        except Exception as exc:  # noqa: BLE001 - REST degrades without the cache
            logger.warning(f"fund_intraday: realtime cache write failed: {exc}")

    # ------------------------------------------------------------------
    # Push: change detection + per-tenant fan-out (§8)
    # ------------------------------------------------------------------

    @staticmethod
    def _result_signature(result: dict) -> tuple:
        """1bp-quantized change + method + status participate in comparison (§8.2);
        nav_estimate is quantized to 4dp because a latest_official answer only
        moves when the official anchor changes."""
        change = result.get("estimate_change_percent")
        quantized_change = round(float(change) * 100) if change is not None else None
        nav = result.get("nav_estimate")
        quantized_nav = round(float(nav), 4) if nav is not None else None
        return (result.get("estimate_method"), result.get("quote_status"), quantized_change, quantized_nav)

    def payload_for(self, tenant_id: str, all_results: list[dict], per_tenant: dict[str, set[str]]) -> list[dict]:
        """Tenant-filtered payload (§8.1 point 4): other tenants never see each
        other's followed codes; the system tenant gets the full array (admin
        observability)."""
        if tenant_id == str(SYSTEM_TENANT_ID):
            return all_results
        followed = per_tenant.get(tenant_id, set())
        if not followed:
            return []
        by_code = {r["symbol"]: r for r in all_results}
        return [by_code[code] for code in sorted(followed) if code in by_code]

    async def _push_batch(self, results: list[dict], per_tenant: dict[str, set[str]]) -> int:
        signatures = {r["symbol"]: self._result_signature(r) for r in results}
        if signatures == _LAST_PUSH_SIGNATURES:
            return 0

        try:
            client = await get_redis_client()
            online = set(await client.smembers(RedisKeys.sse_connected_tenants_finance_key()))
        except Exception as exc:  # noqa: BLE001 - fan-out is best-effort; compute/cache already done
            logger.warning(f"fund_intraday: online-tenant read failed, skipping push: {exc}")
            return 0
        online.add(str(SYSTEM_TENANT_ID))

        pushed = 0
        for tenant_id in sorted(online):
            payload = self.payload_for(tenant_id, results, per_tenant)
            if not payload:
                continue
            try:
                await event_router.push_event(
                    "finance",
                    SSEEventType.NAV_BATCH_UPDATE,
                    payload,
                    tenant_id,
                    history=False,
                )
                pushed += 1
            except Exception as exc:  # noqa: BLE001 - one tenant's fan-out failure never kills the cycle
                logger.warning(f"fund_intraday: nav_batch_update push failed for tenant {tenant_id}: {exc}")

        _LAST_PUSH_SIGNATURES.clear()
        _LAST_PUSH_SIGNATURES.update(signatures)
        return pushed

    # ------------------------------------------------------------------
    # Downsampled DB flush (§3.4)
    # ------------------------------------------------------------------

    async def _flush_results(self, results: list[dict], ids_per_code: dict[str, list]) -> None:
        """Write the estimate rows that pass one of the three admission rules:
        state switch (method/quote_status changed vs. last flush) forces a
        write, otherwise a SET NX EX throttle bounds writes to one per
        fund_nav_db_flush_min_gap window. latest_official rows are skipped —
        they carry no estimate to record. The third rule (close-of-market
        forced snapshot on the open→closed gate edge) lives in the
        module-level write_close_snapshots() so the scheduler can invoke it
        outside the cycle (fund-intraday-nav.md §3.4 case 3)."""
        gap = settings.fund_nav_db_flush_min_gap
        try:
            client = await get_redis_client()
        except Exception as exc:  # noqa: BLE001 - Redis down = flush skipped entirely (logged)
            logger.debug(f"fund_intraday: flush throttle unavailable, skipping DB flush: {exc}")
            return

        due: list[dict] = []
        for r in results:
            method = r["estimate_method"]
            if method == "latest_official":
                continue
            code = r["symbol"]
            mode = (method, r["quote_status"])
            if _LAST_FLUSH_MODES.get(code) != mode:
                # Case 2: state switch — force write and re-anchor the window.
                try:
                    await client.delete(RedisKeys.fund_nav_rt_flush_key(code))
                    await client.set(RedisKeys.fund_nav_rt_flush_key(code), "1", ex=gap)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"fund_intraday: flush window reset failed for {code}: {exc}")
            else:
                try:
                    acquired = await client.set(RedisKeys.fund_nav_rt_flush_key(code), "1", nx=True, ex=gap)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"fund_intraday: flush throttle check failed for {code}: {exc}")
                    continue
                if not acquired:
                    continue
            _LAST_FLUSH_MODES[code] = mode
            due.append(r)

        if not due:
            return

        try:
            written = 0
            for r in due:
                if await self._upsert_flush_row(r, ids_per_code):
                    written += 1
            if written:
                await self.db.commit()
                logger.debug(f"fund_intraday: downsampled {written} estimate row(s)")
        except Exception as exc:  # noqa: BLE001 - flush failure must never kill the cycle
            logger.warning(f"fund_intraday: DB flush failed: {exc}")
            with contextlib.suppress(Exception):
                await self.db.rollback()

    async def _upsert_flush_row(self, result: dict, ids_per_code: dict[str, list]) -> bool:
        """Same-row-override upsert semantics as FinanceService._save_nav_estimate:
        one row per (symbol, method, official NAV date); new official dates open
        new rows. Written under the system tenant (§3.1 scoping)."""
        code = result["symbol"]
        symbol_ids = ids_per_code.get(code)
        if not symbol_ids:
            return False
        symbol_id = sorted(symbol_ids, key=lambda sid: str(sid))[0]

        nav_official_date = None
        if result.get("nav_official_date"):
            try:
                nav_official_date = date.fromisoformat(str(result["nav_official_date"]))
            except ValueError:
                return False
        if nav_official_date is None:
            return False  # no anchor → nothing to record (mirrors _save_nav_estimate)

        stmt = select(FundNAVEstimate).where(
            FundNAVEstimate.symbol_id == symbol_id,
            FundNAVEstimate.estimate_method == result["estimate_method"],
            FundNAVEstimate.nav_official_date == nav_official_date,
        )
        row = (await self.db.execute(stmt)).scalar_one_or_none()
        if row is None:
            row = FundNAVEstimate(
                tenant_id=SYSTEM_TENANT_ID,
                symbol_id=symbol_id,
                estimate_method=result["estimate_method"],
                estimate_timestamp=datetime.now(UTC),
            )
            self.db.add(row)

        row.nav_official = result.get("nav_official")
        row.nav_official_date = nav_official_date
        row.nav_estimate = result.get("nav_estimate")
        row.nav_estimate_deviation_percent = result.get("estimate_change_percent")
        row.estimate_method = result["estimate_method"]
        row.estimate_timestamp = datetime.fromisoformat(result["estimate_timestamp"])
        row.holdings_coverage_percent = result.get("coverage_percent")
        if result.get("holdings_report_date"):
            try:
                row.holdings_report_date = date.fromisoformat(str(result["holdings_report_date"]))
            except ValueError:
                row.holdings_report_date = None
        else:
            row.holdings_report_date = None
        return True


async def write_close_snapshots(session) -> int:
    """Close-of-market forced snapshot write (fund-intraday-nav.md §3.4 case 3).

    Called by the scheduler on the open→closed gate edge with the same service
    session the cycles used. Flushes the LAST computed estimates for every fund
    that carried a holdings_weighted/index_tracking estimate, regardless of the
    downsample throttle window, so fund_nav_estimates holds the closing snapshot
    (fresh fetches are impossible at the edge — the market is already closed).
    Also clears the throttle keys and flush-mode state so the next session's
    first in-session write is not swallowed by a stale window. Returns rows
    written; never raises (the gate path must stay exception-free)."""
    results_by_code = _LAST_CYCLE_CONTEXT.get("results_by_code") or {}
    ids_per_code = _LAST_CYCLE_CONTEXT.get("ids_per_code") or {}
    due = [r for r in results_by_code.values() if r.get("estimate_method") in ("holdings_weighted", "index_tracking")]

    written = 0
    if due:
        svc = FundIntradayService(db=session)
        for result in due:
            try:
                if await svc._upsert_flush_row(result, ids_per_code):
                    written += 1
            except Exception as exc:  # noqa: BLE001 - one fund's failure must not drop the rest
                logger.warning(f"fund_intraday: close snapshot upsert failed for {result.get('symbol')}: {exc}")
        if written:
            try:
                await session.commit()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"fund_intraday: close snapshot commit failed: {exc}")
                with contextlib.suppress(Exception):
                    await session.rollback()
                written = 0

    # Reset throttle windows + flush modes for the next trading session.
    try:
        client = await get_redis_client()
        for result in due:
            await client.delete(RedisKeys.fund_nav_rt_flush_key(result["symbol"]))
    except Exception as exc:  # noqa: BLE001 - stale windows merely delay the next write
        logger.debug(f"fund_intraday: close throttle reset failed: {exc}")

    _LAST_FLUSH_MODES.clear()
    _LAST_CYCLE_CONTEXT.clear()
    logger.info(f"fund_intraday: close-of-market snapshot wrote {written} row(s)")
    return written
