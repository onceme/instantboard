import hashlib
import json
import logging
from datetime import UTC, date, datetime

from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import YAHOO_BROWSER_HEADERS
from app.core.exceptions import (
    DuplicateWatchlistItem,
    ServiceUnavailable,
    SymbolNotFound,
    ValidationError,
)
from app.core.redis import RedisKeys, get_redis_client, redis_delete, redis_get, redis_set
from app.core.sse_router import SSEEventType, event_router
from app.models.finance import FinanceQuote, FinanceSymbol, FundNAVEstimate
from app.models.watchlist import WatchlistItem
from app.services import fund_registry
from app.services.market_calendar import (
    CLOSED_REASON_HOLIDAY,
    MARKET_TIMEZONES,
    MARKET_TRADING_HOURS,
    get_market_holiday_name,
    is_market_holiday,
    market_closed_reason,
)

logger = logging.getLogger(__name__)

MARKET_INDICES_CONFIG = [
    {"symbol": "^GSPC", "name": "S&P 500", "region": "US"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial Average", "region": "US"},
    {"symbol": "^IXIC", "name": "NASDAQ Composite", "region": "US"},
    {"symbol": "000001.SS", "name": "上证综合指数", "region": "CN"},
    {"symbol": "399001.SZ", "name": "深证成份指数", "region": "CN"},
    {"symbol": "000300.SS", "name": "沪深300", "region": "CN"},
    {"symbol": "^HSI", "name": "恒生指数", "region": "HK"},
    {"symbol": "^N225", "name": "日经225", "region": "JP"},
    {"symbol": "^FTSE", "name": "FTSE 100", "region": "GB"},
    {"symbol": "^GDAXI", "name": "DAX 40", "region": "DE"},
    {"symbol": "^FCHI", "name": "CAC 40", "region": "FR"},
    {"symbol": "^KS11", "name": "KOSPI", "region": "KR"},
    {"symbol": "^BSESN", "name": "BSE Sensex", "region": "IN"},
]

COMMODITIES_CONFIG = [
    {"symbol": "GC=F", "name": "黄金期货", "unit": "USD/oz"},
    {"symbol": "SI=F", "name": "白银期货", "unit": "USD/oz"},
    {"symbol": "CL=F", "name": "WTI原油期货", "unit": "USD/bbl"},
    {"symbol": "BZ=F", "name": "Brent原油期货", "unit": "USD/bbl"},
    {"symbol": "NG=F", "name": "天然气期货", "unit": "USD/MMBtu"},
    {"symbol": "HG=F", "name": "铜期货", "unit": "USD/lb"},
    {"symbol": "ZS=F", "name": "大豆期货", "unit": "USD/bushel"},
]

# MARKET_TIMEZONES / MARKET_TRADING_HOURS live in app.services.market_calendar
# together with the static holiday tables (finance-tab.md §3.4.4); they are
# re-imported above so existing references keep their import paths.

REDIS_TTL_QUOTE = 30
REDIS_TTL_MARKET_INDEX = 60
REDIS_TTL_COMMODITY = 60
REDIS_TTL_NAV = 120
REDIS_TTL_SEARCH = 300
REDIS_TTL_WATCHLIST = 600

MAX_WATCHLIST_ITEMS = 512

# Watchlist price-alert threshold bounds in percent (finance-tab.md §3.2).
# Enforced in update_watchlist_alert_threshold with a 400 VALIDATION_ERROR so
# the response carries the app error envelope instead of a FastAPI 422.
ALERT_THRESHOLD_MIN = 0.5
ALERT_THRESHOLD_MAX = 50.0

# Failover data-type identifiers, promoted to module-level constants to prevent a recurrence
# of the "market_indices"/"market_index" spelling mismatch (previously
# _fetch_indices_with_failover passed "market_indices" while the chain matched
# "market_index", so the indices chain always fell through to the fallback).
DATA_TYPE_STOCK_QUOTE = "stock_quote"
DATA_TYPE_MARKET_INDICES = "market_indices"
DATA_TYPE_COMMODITY = "commodity"

# fund_nav_estimates row kinds (estimate_method column): "official" rows are
# written by the daily fund_nav_official_refresh job (update_official_nav),
# "index_tracking" rows by get_fund_nav when a realtime estimate succeeds.
ESTIMATE_METHOD_OFFICIAL = "official"
ESTIMATE_METHOD_INDEX_TRACKING = "index_tracking"


class FinanceService:
    def __init__(self, db: AsyncSession, redis: Redis | None = None):
        # redis is optional: the market indices/commodities refresh path only uses the
        # module-level redis_get/redis_set helpers, so background (scheduler) callers can
        # construct the service with a DB session alone.
        self.db = db
        self.redis = redis

    async def search_symbols(
        self,
        tenant_id: str,
        q: str,
        type: str | None = None,  # noqa: A002
        market: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        query_hash = hashlib.md5(f"{q}:{type}:{market}".encode()).hexdigest()
        cache_key = f"t:{tenant_id}:search:{query_hash}"
        cached = await redis_get(cache_key)
        if cached:
            try:
                all_results = json.loads(cached)
                return self._paginate_results(all_results, page, page_size)
            except (json.JSONDecodeError, TypeError):
                pass

        stmt = select(FinanceSymbol).where(
            FinanceSymbol.tenant_id == tenant_id,
            FinanceSymbol.is_active,
        )

        if type and type != "all":
            stmt = stmt.where(FinanceSymbol.type == type)
        if market and market != "all":
            stmt = stmt.where(FinanceSymbol.market == market)

        exact_match = or_(FinanceSymbol.symbol == q.upper(), FinanceSymbol.name == q)
        prefix_match = or_(
            FinanceSymbol.symbol.ilike(f"{q.upper()}%"),
            FinanceSymbol.name.ilike(f"{q}%"),
        )
        fuzzy_match = or_(
            FinanceSymbol.symbol.ilike(f"%{q.upper()}%"),
            FinanceSymbol.name.ilike(f"%{q}%"),
        )

        stmt_exact = stmt.where(exact_match).order_by(FinanceSymbol.symbol)
        stmt_prefix = stmt.where(prefix_match).order_by(FinanceSymbol.symbol)
        stmt_fuzzy = stmt.where(fuzzy_match).order_by(FinanceSymbol.symbol)

        results = []
        seen_symbols = set()
        seen_fund_codes = set()

        for stmt_level in [stmt_exact, stmt_prefix, stmt_fuzzy]:
            rows = await self.db.execute(stmt_level)
            for row in rows.scalars().all():
                if row.symbol not in seen_symbols:
                    seen_symbols.add(row.symbol)
                    fund_code_norm = self._normalize_fund_code(row.symbol)
                    if fund_code_norm:
                        seen_fund_codes.add(fund_code_norm)
                    quote_data = await self._get_cached_quote(tenant_id, row.symbol)
                    results.append(
                        {
                            "symbol": row.symbol,
                            "name": row.name,
                            "type": row.type,
                            "market": row.market,
                            "exchange": row.exchange,
                            "current_price": quote_data.get("current_price") if quote_data else None,
                            "change_percent": quote_data.get("change_percent") if quote_data else None,
                            "currency": row.currency,
                        }
                    )

        # Fund registry is the authoritative catalog of every registered CN fund
        # (fund-intraday-nav.md §9.3): exact codes resolve with real names ahead
        # of the external indexes, and text/pinyin input gains OTC candidates no
        # external index knows. get_registry() degrades to None on any failure —
        # the heuristic/external paths below then carry the query as before.
        fund_type_ok = type in (None, "all", "fund")
        fund_market_ok = market in (None, "all", "CN")
        registry = await fund_registry.get_registry() if fund_type_ok and fund_market_ok else None

        registry_candidates: list[dict] = []
        if registry is not None:
            fund_code_query = self._normalize_fund_code(q)
            if fund_code_query:
                entry = registry.lookup_code(fund_code_query)
                if entry is not None:
                    hit = await self._register_registry_fund(tenant_id, entry)
                    if entry.code in seen_fund_codes:
                        # The DB tier already surfaced a spelling variant of this
                        # code — sync its displayed name to the authoritative one
                        # (heals the legacy "基金 {code}" placeholder rows).
                        for item in results:
                            if self._normalize_fund_code(item["symbol"]) == entry.code:
                                item["name"] = hit["name"]
                    else:
                        seen_symbols.add(entry.code)
                        seen_fund_codes.add(entry.code)
                        results.append(hit)
                    # Registry hit outranks the external indexes for exact codes:
                    # OTC funds are absent from them anyway.
                    if results:
                        await redis_set(cache_key, json.dumps(results), ex=REDIS_TTL_SEARCH)
                    return self._paginate_results(results, page, page_size)
            else:
                # Candidates are returned WITHOUT pre-registering tenant symbols
                # (a keystroke search might match 20 funds); registration happens
                # at selection time — add_to_watchlist's registry fallback.
                for entry in registry.search(q):
                    if entry.code in seen_fund_codes or entry.code in seen_symbols:
                        continue
                    seen_symbols.add(entry.code)
                    seen_fund_codes.add(entry.code)
                    registry_candidates.append(self._registry_candidate_result(entry))

        if not results and not registry_candidates:
            external_results = await self._search_symbols_external(q, tenant_id)
            for ext in external_results:
                if ext["symbol"] not in seen_symbols:
                    seen_symbols.add(ext["symbol"])
                    fund_code_norm = self._normalize_fund_code(ext["symbol"])
                    if fund_code_norm:
                        seen_fund_codes.add(fund_code_norm)
                    results.append(ext)

        results.extend(registry_candidates)

        # CN fund-code fallback (fund-intraday-nav.md, M2 phase A): the registry
        # now owns code resolution when loaded; this heuristic path remains the
        # degradation target for registry outages and codes missing from the
        # catalog. External indexes do not know OTC open-end funds and search
        # only ever created symbols from upstream hits, so without this a bare
        # fund code could never enter a watchlist.
        if not results and (type in (None, "all", "fund")):
            auto_registered = await self._maybe_autoregister_fund_code(tenant_id, q)
            if auto_registered is not None:
                results.append(auto_registered)

        if results:
            await redis_set(cache_key, json.dumps(results), ex=REDIS_TTL_SEARCH)

        return self._paginate_results(results, page, page_size)

    async def get_quote(self, tenant_id: str, symbol: str, detail_level: str = "basic") -> dict:
        quote_data = await self._get_cached_quote(tenant_id, symbol)
        if quote_data:
            return quote_data

        quote_data = await self._fetch_quote_with_failover(tenant_id, symbol)
        if not quote_data:
            raise SymbolNotFound(message=f"Quote not available for symbol: {symbol}")

        await self._store_quote_to_db(tenant_id, symbol, quote_data)
        await self._cache_quote(tenant_id, symbol, quote_data)

        await event_router.push_event(
            "finance",
            SSEEventType.QUOTE_UPDATE,
            {
                "symbol": symbol,
                "name": quote_data.get("name", symbol),
                "current_price": quote_data.get("current_price"),
                "change": quote_data.get("change"),
                "change_percent": quote_data.get("change_percent"),
                "volume": quote_data.get("volume"),
                "timestamp": quote_data.get("timestamp"),
            },
            tenant_id,
        )

        return quote_data

    async def get_market_indices(self, tenant_id: str) -> list[dict]:
        cache_key = RedisKeys.market_indices_key(tenant_id)
        cached = await redis_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        formatted = await self._refresh_market_indices(tenant_id)
        if formatted is None:
            raise ServiceUnavailable(message="Market indices data temporarily unavailable")
        return formatted

    async def refresh_market_indices(self, tenant_id: str) -> bool:
        """Periodic refresh entry (market_indices_refresh job, finance-tab.md §3.8.2).

        Shares the exact fetch + cache-write + SSE-push path with a get_market_indices
        cache miss; returns success instead of raising so the scheduled job body can log
        and move on. The cache is always rewritten (renews the TTL); the SSE push is
        skipped when the payload is unchanged from the current cache.
        """
        return await self._refresh_market_indices(tenant_id, skip_unchanged_push=True) is not None

    async def _refresh_market_indices(self, tenant_id: str, skip_unchanged_push: bool = False) -> list[dict] | None:
        results = await self._fetch_indices_with_failover(tenant_id)

        if not results:
            return None

        formatted = self._format_market_indices(results)

        cache_key = RedisKeys.market_indices_key(tenant_id)
        previous = await redis_get(cache_key) if skip_unchanged_push else None
        await redis_set(cache_key, json.dumps(formatted), ex=REDIS_TTL_MARKET_INDEX)

        # Scheduled refresh (skip_unchanged_push=True) may skip the SSE push when nothing
        # changed; the on-demand cache-miss path keeps pushing unconditionally.
        if not (skip_unchanged_push and self._payload_unchanged(previous, formatted)):
            await event_router.push_event(
                "finance",
                SSEEventType.MARKET_INDEX_UPDATE,
                formatted,
                tenant_id,
            )

        return formatted

    def _format_market_indices(self, results: list[dict]) -> list[dict]:
        formatted = []
        for config in MARKET_INDICES_CONFIG:
            idx_data = None
            for r in results:
                if r.get("symbol") == config["symbol"]:
                    idx_data = r
                    break

            status_info = self.get_market_status(config["region"])

            formatted.append(
                {
                    "symbol": config["symbol"],
                    "name": config["name"],
                    "value": idx_data.get("current_price") if idx_data else None,
                    "change": idx_data.get("change") if idx_data else None,
                    "change_percent": idx_data.get("change_percent") if idx_data else None,
                    "market_status": status_info["status"],
                    # weekend/holiday/off_hours while closed, None while open
                    "market_status_reason": status_info["reason"],
                    "holiday_name": status_info.get("holiday_name"),
                    "region": config["region"],
                    "timestamp": idx_data.get("timestamp") if idx_data else None,
                }
            )
        return formatted

    async def get_commodities(self, tenant_id: str) -> list[dict]:
        cache_key = RedisKeys.commodities_key(tenant_id)
        cached = await redis_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        formatted = await self._refresh_commodities(tenant_id)
        if formatted is None:
            raise ServiceUnavailable(message="Commodity data temporarily unavailable")
        return formatted

    async def refresh_commodities(self, tenant_id: str) -> bool:
        """Periodic refresh entry (commodities_refresh job, finance-tab.md §3.8.2).

        Shares the exact fetch + cache-write + SSE-push path with a get_commodities cache
        miss; returns success instead of raising so the scheduled job body can log and
        move on. The cache is always rewritten (renews the TTL); the SSE push is skipped
        when the payload is unchanged from the current cache.
        """
        return await self._refresh_commodities(tenant_id, skip_unchanged_push=True) is not None

    async def _refresh_commodities(self, tenant_id: str, skip_unchanged_push: bool = False) -> list[dict] | None:
        results = await self._fetch_commodities_with_failover(tenant_id)

        if not results:
            return None

        formatted = self._format_commodities(results)

        cache_key = RedisKeys.commodities_key(tenant_id)
        previous = await redis_get(cache_key) if skip_unchanged_push else None
        await redis_set(cache_key, json.dumps(formatted), ex=REDIS_TTL_COMMODITY)

        if not (skip_unchanged_push and self._payload_unchanged(previous, formatted)):
            await event_router.push_event(
                "finance",
                SSEEventType.COMMODITY_UPDATE,
                formatted,
                tenant_id,
            )

        return formatted

    def _format_commodities(self, results: list[dict]) -> list[dict]:
        formatted = []
        for config in COMMODITIES_CONFIG:
            comm_data = None
            for r in results:
                if r.get("symbol") == config["symbol"]:
                    comm_data = r
                    break

            formatted.append(
                {
                    "symbol": config["symbol"],
                    "name": config["name"],
                    "value": comm_data.get("current_price") if comm_data else None,
                    "change": comm_data.get("change") if comm_data else None,
                    "change_percent": comm_data.get("change_percent") if comm_data else None,
                    "unit": config["unit"],
                    "timestamp": comm_data.get("timestamp") if comm_data else None,
                }
            )
        return formatted

    @staticmethod
    def _payload_unchanged(cached: str | None, formatted: list[dict]) -> bool:
        # Change detection for the periodic refresh: skip the SSE push when the freshly
        # formatted payload equals the cached one. Any unreadable/missing cache counts as
        # changed (push).
        if not cached:
            return False
        try:
            return json.loads(cached) == formatted
        except (json.JSONDecodeError, TypeError):
            return False

    async def get_fund_nav(self, tenant_id: str, symbol: str, estimate_type: str = "realtime") -> dict:
        # Intraday fast path (fund-intraday-nav.md §9.1): while the CN market is
        # open the worker refresh loop keeps fund_nav_rt:{code} current (TTL 12s),
        # so a fresh hit answers directly and skips both the recompute and the
        # NAV_ESTIMATE_UPDATE side-effect push of the legacy path.
        fund_code_fast = self._normalize_fund_code(symbol)
        if fund_code_fast:
            try:
                rt_cached = await redis_get(RedisKeys.fund_nav_rt_key(fund_code_fast))
            except Exception as e:
                logger.debug(f"fund_nav_rt read failed for {fund_code_fast}: {e}")
                rt_cached = None
            if rt_cached:
                try:
                    rt_payload = json.loads(rt_cached)
                    if isinstance(rt_payload, dict) and rt_payload.get("symbol"):
                        return rt_payload
                except (json.JSONDecodeError, TypeError):
                    pass

        nav_cache_key = RedisKeys.nav_key(tenant_id, symbol)
        cached = await redis_get(nav_cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        stmt = select(FinanceSymbol).where(
            FinanceSymbol.tenant_id == tenant_id,
            FinanceSymbol.symbol == symbol,
            FinanceSymbol.is_active,
        )
        result = await self.db.execute(stmt)
        fund = result.scalar_one_or_none()

        if not fund:
            # Fund-code fallback (M2 phase A): bare-code and suffixed spellings
            # of the same fund resolve interchangeably (seeds use suffixed
            # symbols for listed ETFs, auto-registration uses bare OTC codes).
            nav_fund_code = self._normalize_fund_code(symbol)
            if nav_fund_code:
                variants = [nav_fund_code, f"{nav_fund_code}.SS", f"{nav_fund_code}.SZ", f"{nav_fund_code}.OF"]
                retry_stmt = (
                    select(FinanceSymbol)
                    .where(
                        FinanceSymbol.tenant_id == tenant_id,
                        FinanceSymbol.type == "fund",
                        FinanceSymbol.is_active,
                        func.upper(FinanceSymbol.symbol).in_([v.upper() for v in variants]),
                    )
                    .order_by(FinanceSymbol.symbol)
                    .limit(1)
                )
                fund = (await self.db.execute(retry_stmt)).scalar_one_or_none()

        if not fund:
            raise SymbolNotFound(message=f"Fund symbol not found: {symbol}")

        # Official NAV: the latest row that actually carries one, newest NAV
        # date first (the daily fund_nav_official_refresh job writes those
        # rows; nulls_last because PG puts NULLs first on DESC by default).
        official_stmt = (
            select(FundNAVEstimate)
            .where(
                FundNAVEstimate.tenant_id == tenant_id,
                FundNAVEstimate.symbol_id == fund.id,
                FundNAVEstimate.nav_official.is_not(None),
            )
            .order_by(
                FundNAVEstimate.nav_official_date.desc().nulls_last(),
                FundNAVEstimate.estimate_timestamp.desc(),
            )
            .limit(1)
        )
        official_result = await self.db.execute(official_stmt)
        official_record = official_result.scalar_one_or_none()

        # Latest row of any kind (legacy query): source of the previous
        # estimate values and of the underlying-index binding.
        nav_stmt = (
            select(FundNAVEstimate)
            .where(
                FundNAVEstimate.tenant_id == tenant_id,
                FundNAVEstimate.symbol_id == fund.id,
            )
            .order_by(FundNAVEstimate.estimate_timestamp.desc())
            .limit(1)
        )
        nav_result = await self.db.execute(nav_stmt)
        nav_record = nav_result.scalar_one_or_none()

        nav_official = None
        nav_official_date = None
        nav_official_date_obj: date | None = None
        nav_estimate = None
        nav_estimate_deviation_percent = None
        estimate_method = None
        underlying_index_info = None

        # Official value prefers the dedicated official row; rows without one
        # (legacy/manual data) still surface their stored value. No row at all
        # leaves everything None — the response stays a gentle "no official NAV
        # yet" shape, never an error.
        official_source = official_record if official_record is not None else nav_record
        if official_source is not None:
            nav_official = float(official_source.nav_official) if official_source.nav_official else None
            nav_official_date = str(official_source.nav_official_date) if official_source.nav_official_date else None
            nav_official_date_obj = official_source.nav_official_date

        # Index binding (fund-intraday-nav.md §3.3 — dead-end fix): the
        # fund_index_bindings table is the source of truth (seeded for
        # mainstream broad-base funds); resolve_index_binding falls back to a
        # residual underlying_index_symbol row for legacy estimates. Without a
        # binding the realtime branch below simply does not engage, as before.
        bound_index_symbol: str | None = None
        tracking_ratio = 1.0
        binding_code = self._normalize_fund_code(symbol)
        if binding_code:
            from app.services.fund_holdings import FundHoldingsService

            binding_pair = await FundHoldingsService(db=self.db).resolve_index_binding(binding_code, fund.id)
            if binding_pair is not None:
                bound_index_symbol, tracking_ratio = binding_pair
        if bound_index_symbol is None and nav_record and nav_record.underlying_index_symbol:
            # Non-6-digit symbols cannot hit the bindings table; keep the
            # legacy row-level binding usable for them.
            bound_index_symbol = nav_record.underlying_index_symbol

        if nav_record:
            nav_estimate = float(nav_record.nav_estimate) if nav_record.nav_estimate else None
            nav_estimate_deviation_percent = (
                float(nav_record.nav_estimate_deviation_percent) if nav_record.nav_estimate_deviation_percent else None
            )
            estimate_method = nav_record.estimate_method

        if bound_index_symbol:
            index_quote = await self._get_cached_quote(tenant_id, bound_index_symbol)
            # Initialize from the stored snapshot only when it belongs to the
            # same index (bindings can differ from a legacy row's snapshot).
            snapshot_value = None
            snapshot_change = None
            if nav_record and nav_record.underlying_index_symbol == bound_index_symbol:
                snapshot_value = float(nav_record.underlying_index_value) if nav_record.underlying_index_value else None
                snapshot_change = (
                    float(nav_record.underlying_index_change_percent)
                    if nav_record.underlying_index_change_percent
                    else None
                )
            underlying_index_info = {
                "symbol": bound_index_symbol,
                "name": bound_index_symbol,
                "current_value": snapshot_value,
                "change_percent": snapshot_change,
            }
            if index_quote:
                underlying_index_info["name"] = index_quote.get("name", bound_index_symbol)
                underlying_index_info["current_value"] = index_quote.get("current_price")
                underlying_index_info["change_percent"] = index_quote.get("change_percent")

        if estimate_type == "realtime" and fund.type == "fund" and nav_official and underlying_index_info:
            index_change = underlying_index_info.get("change_percent", 0) or 0
            nav_estimate = nav_official * (1 + index_change / 100 * tracking_ratio)
            nav_estimate_deviation_percent = (
                round((nav_estimate - nav_official) / nav_official * 100, 4) if nav_official else None
            )
            estimate_method = ESTIMATE_METHOD_INDEX_TRACKING
            # Persist the successful estimate (previously Redis-only); the
            # Redis cache semantics below are unchanged.
            await self._save_nav_estimate(
                tenant_id=tenant_id,
                fund=fund,
                nav_official=nav_official,
                nav_official_date=nav_official_date_obj,
                nav_estimate=nav_estimate,
                nav_estimate_deviation_percent=nav_estimate_deviation_percent,
                underlying_index_info=underlying_index_info,
            )

        response = {
            "symbol": symbol,
            "name": fund.name,
            "nav_official": nav_official,
            "nav_official_date": nav_official_date,
            "nav_estimate": nav_estimate,
            "nav_estimate_deviation_percent": nav_estimate_deviation_percent,
            "estimate_method": estimate_method,
            "estimate_timestamp": datetime.now(UTC).isoformat(),
            "underlying_index": underlying_index_info,
        }

        await redis_set(nav_cache_key, json.dumps(response), ex=REDIS_TTL_NAV)

        await event_router.push_event(
            "finance",
            SSEEventType.NAV_ESTIMATE_UPDATE,
            {
                "symbol": symbol,
                "name": fund.name,
                "nav_official": nav_official,
                "nav_estimate": nav_estimate,
                "nav_estimate_deviation_percent": nav_estimate_deviation_percent,
                "estimate_method": estimate_method,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            tenant_id,
        )

        return response

    async def _save_nav_estimate(
        self,
        tenant_id: str,
        fund: FinanceSymbol,
        nav_official: float,
        nav_official_date: date | None,
        nav_estimate: float,
        nav_estimate_deviation_percent: float | None,
        underlying_index_info: dict | None,
    ) -> None:
        """Persist a freshly computed realtime estimate into fund_nav_estimates.

        Upsert policy: one estimate row per (fund, official NAV date) —
        repeated intraday estimates against the same official NAV overwrite
        the same row (table stays bounded); each new official NAV date starts
        a new row. Rows are keyed by estimate_method=index_tracking so they
        never collide with the daily "official" rows written by
        update_official_nav. Rows are keyed by the official NAV date, so an
        estimate without a known official NAV date is not persisted. A
        persistence failure never breaks the read path: it is logged, rolled
        back, and the response still returns (the Redis cache is unaffected).
        """
        if nav_official_date is None:
            return

        try:
            stmt = select(FundNAVEstimate).where(
                FundNAVEstimate.tenant_id == tenant_id,
                FundNAVEstimate.symbol_id == fund.id,
                FundNAVEstimate.estimate_method == ESTIMATE_METHOD_INDEX_TRACKING,
                FundNAVEstimate.nav_official_date == nav_official_date,
            )
            result = await self.db.execute(stmt)
            row = result.scalar_one_or_none()

            now = datetime.now(UTC)
            if row is None:
                row = FundNAVEstimate(
                    tenant_id=tenant_id,
                    symbol_id=fund.id,
                    estimate_timestamp=now,
                )
                self.db.add(row)

            row.nav_official = nav_official
            row.nav_official_date = nav_official_date
            row.nav_estimate = nav_estimate
            row.nav_estimate_deviation_percent = nav_estimate_deviation_percent
            row.estimate_method = ESTIMATE_METHOD_INDEX_TRACKING
            row.estimate_timestamp = now
            row.underlying_index_symbol = (underlying_index_info or {}).get("symbol")
            row.underlying_index_value = (underlying_index_info or {}).get("current_value")
            row.underlying_index_change_percent = (underlying_index_info or {}).get("change_percent")

            await self.db.commit()
        except Exception as e:
            logger.warning(f"Failed to persist NAV estimate for {fund.symbol}: {e}")
            await self.db.rollback()

    # --- Intraday NAV REST surface (fund-intraday-nav.md §9.1) ---

    async def _read_fund_nav_rt(self, fund_code: str) -> dict | None:
        """Fresh realtime estimate from the worker loop's fund_nav_rt cache."""
        try:
            raw = await redis_get(RedisKeys.fund_nav_rt_key(fund_code))
        except Exception as e:
            logger.debug(f"fund_nav_rt read failed for {fund_code}: {e}")
            return None
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        return payload if isinstance(payload, dict) and payload.get("symbol") else None

    @staticmethod
    def _fund_nav_error_entry(symbol: str, message: str) -> dict:
        """Per-code error entry: unknown/degenerate codes degrade to an entry
        instead of failing the whole batch (§9.1)."""
        return {
            "symbol": symbol or "",
            "name": symbol or "",
            "estimate_method": "latest_official",
            "quote_status": "frozen",
            "estimate_timestamp": datetime.now(UTC).isoformat(),
            "error": message,
        }

    async def _fund_nav_latest_official_entries(self, codes: list[str]) -> dict[str, dict]:
        """Lightweight latest_official fallback for rt-cache misses: anchor +
        official date from fund_nav_estimates only — never triggers holdings
        ingestion or a quote fetch (the ingest hook is fired separately by
        get_fund_nav_batch when a snapshot is missing)."""
        from app.services.fund_intraday import FundIntradayService

        now_iso = datetime.now(UTC).isoformat()
        code_set = set(codes)
        intraday = FundIntradayService(db=self.db)
        ids_per_code, name_per_code = await intraday._load_symbol_map(code_set)
        anchors = await intraday._load_official_anchors(ids_per_code)

        out: dict[str, dict] = {}
        for code in codes:
            if code not in ids_per_code:
                out[code] = self._fund_nav_error_entry(code, "fund symbol not found")
                continue
            anchor = anchors.get(code)
            out[code] = {
                "symbol": code,
                "name": name_per_code.get(code, code),
                "nav_official": anchor[0] if anchor else None,
                "nav_official_date": str(anchor[1]) if anchor and anchor[1] else None,
                "nav_estimate": anchor[0] if anchor else None,
                "estimate_change_percent": None,
                "estimate_method": "latest_official",
                "coverage_percent": None,
                "holdings_report_date": None,
                "quote_status": "frozen",
                "delayed_markets": [],
                "holdings_stale": False,
                "estimate_timestamp": now_iso,
            }
        return out

    async def get_fund_nav_batch(self, tenant_id: str, symbols: list[str]) -> list[dict]:
        """Batch intraday NAV lookup (§9.1): ≤50 symbols, de-duplicated and
        normalized to 6-digit codes. rt-cache hit → live estimate; miss →
        latest_official fallback and a lazy holdings-ingest hook for codes
        without a snapshot; unknown codes → error entry (never a 404)."""
        raw_to_code: list[tuple[str, str | None]] = []
        ordered_codes: list[str] = []
        seen: set[str] = set()
        for raw in symbols:
            raw_clean = str(raw or "").strip()
            code = self._normalize_fund_code(raw_clean)
            raw_to_code.append((raw_clean, code))
            if code and code not in seen:
                seen.add(code)
                ordered_codes.append(code)

        results_by_code: dict[str, dict] = {}
        if ordered_codes:
            for code in ordered_codes:
                payload = await self._read_fund_nav_rt(code)
                if payload is not None:
                    results_by_code[code] = payload
            missing = [code for code in ordered_codes if code not in results_by_code]
            if missing:
                results_by_code.update(await self._fund_nav_latest_official_entries(missing))
                # Lazy holdings ingest (§5.1 case 3): fire-and-forget only for
                # codes that truly lack a usable snapshot; the current request
                # already got its lightweight answer.
                try:
                    from app.services.fund_holdings import FundHoldingsService, spawn_holdings_ingestion

                    needing = await FundHoldingsService(db=self.db).codes_needing_ingestion(missing)
                    for code in sorted(needing):
                        spawn_holdings_ingestion(code)
                except Exception as e:
                    logger.debug(f"fund-nav batch: lazy ingest hook failed: {e}")

        entries: list[dict] = []
        emitted_invalid: set[str] = set()
        for raw_clean, code in raw_to_code:
            if code is None:
                if raw_clean in emitted_invalid:
                    continue
                emitted_invalid.add(raw_clean)
                entries.append(self._fund_nav_error_entry(raw_clean, "unrecognized fund code"))
                continue
            entries.append(results_by_code.get(code) or self._fund_nav_error_entry(code, "no data available"))
        return entries

    async def _quote_symbols_types(self, tenant_id: str, symbols: list[str]) -> dict[str, str]:
        symbols = [s for s in symbols if s]
        if not symbols:
            return {}
        stmt = select(FinanceSymbol.symbol, FinanceSymbol.type).where(
            FinanceSymbol.tenant_id == tenant_id,
            FinanceSymbol.symbol.in_(symbols),
        )
        result = await self.db.execute(stmt)
        return {symbol: fund_type for symbol, fund_type in result.all()}

    async def update_official_nav(self, tenant_id: str) -> int:
        """Daily official NAV refresh (fund_nav_official_refresh job, 20:00
        Asia/Shanghai, finance-tab.md §3.8.2).

        Fetches the latest official unit NAV + NAV date for every active
        type='fund' FinanceSymbol of the tenant via TiantianFundCollector
        (EastMoney f10 lsjz API) and upserts one "official" row per
        (fund, NAV date) into fund_nav_estimates — a re-run on the same day
        updates the same row instead of duplicating it. System-tenant scoped
        like the other display-chain refresh jobs. Never raises on collection
        trouble: a failed/empty collector round logs and returns 0 so the
        scheduled job survives; returns the number of funds updated.
        """
        stmt = select(FinanceSymbol).where(
            FinanceSymbol.tenant_id == tenant_id,
            FinanceSymbol.type == "fund",
            FinanceSymbol.is_active,
        )
        result = await self.db.execute(stmt)
        funds = list(result.scalars().all())
        if not funds:
            logger.info("update_official_nav: no fund symbols for tenant, nothing to do")
            return 0

        # Tiantian Fund addresses funds by bare 6-digit codes; symbols stored
        # in other shapes (Yahoo suffixes, external tickers) are skipped.
        code_map: dict[str, FinanceSymbol] = {}
        for fund in funds:
            code = self._normalize_fund_code(fund.symbol)
            if code and code not in code_map:
                code_map[code] = fund
        if not code_map:
            logger.info("update_official_nav: no fund symbols map to Chinese fund codes")
            return 0

        try:
            from app.collectors.finance.fund_nav_collector import TiantianFundCollector

            collector = TiantianFundCollector()
            source = _MockSource(
                tenant_id=tenant_id,
                name=TiantianFundCollector.SOURCE_NAME,
                config={"fund_codes": list(code_map)},
            )
            collection = await collector.collect(source)
        except Exception as e:
            logger.warning(f"update_official_nav: collector run failed: {e}")
            return 0

        if not collection.success or not collection.items:
            logger.warning(
                f"update_official_nav: collector returned no data "
                f"(success={collection.success}, error={collection.error})"
            )
            return 0

        updated = 0
        now = datetime.now(UTC)
        for item in collection.items:
            fund = code_map.get(str(item.get("symbol")))
            nav = item.get("nav")
            nav_date_raw = item.get("nav_date")
            if fund is None or not nav or not nav_date_raw:
                continue
            try:
                nav_date = date.fromisoformat(str(nav_date_raw))
            except ValueError:
                logger.debug(f"update_official_nav: unparsable NAV date {nav_date_raw!r} for {fund.symbol}")
                continue

            upsert_stmt = select(FundNAVEstimate).where(
                FundNAVEstimate.tenant_id == tenant_id,
                FundNAVEstimate.symbol_id == fund.id,
                FundNAVEstimate.estimate_method == ESTIMATE_METHOD_OFFICIAL,
                FundNAVEstimate.nav_official_date == nav_date,
            )
            upsert_result = await self.db.execute(upsert_stmt)
            row = upsert_result.scalar_one_or_none()
            if row is None:
                row = FundNAVEstimate(
                    tenant_id=tenant_id,
                    symbol_id=fund.id,
                    estimate_timestamp=now,
                )
                self.db.add(row)

            row.nav_official = nav
            row.nav_official_date = nav_date
            row.estimate_method = ESTIMATE_METHOD_OFFICIAL
            row.estimate_timestamp = now
            updated += 1

        if updated:
            await self.db.commit()
            logger.info(f"update_official_nav: updated official NAV for {updated} fund(s)")
        else:
            logger.info("update_official_nav: collector items did not match any tracked fund")
        return updated

    @staticmethod
    def _normalize_fund_code(symbol: str) -> str | None:
        """Reduce a finance_symbols entry to a 6-digit Chinese fund code.

        Tiantian Fund only addresses funds by bare 6-digit codes, so
        Yahoo-style suffixes are stripped (510300.SS → 510300); anything that
        does not end up as exactly 6 digits is not collectable and is skipped
        by update_official_nav.
        """
        code = str(symbol or "").strip().upper()
        for suffix in (".SS", ".SZ", ".OF"):
            if code.endswith(suffix):
                code = code[: -len(suffix)]
                break
        return code if len(code) == 6 and code.isdigit() else None

    async def _invalidate_watchlist_cache(self, tenant_id: str, user_id: str) -> None:
        """Delete the watchlist cache key (database.md §3.2). Never raises: the
        callers either already committed the mutation to PG or are about to fall
        back to a PG query, so a failed delete against a down Redis cannot serve
        stale data nor turn the request into an error."""
        cache_key = RedisKeys.watchlist_key(tenant_id, user_id)
        try:
            await redis_delete(cache_key)
        except Exception as e:
            logger.debug(f"Watchlist cache invalidation failed for {cache_key}: {e}")

    async def get_watchlist(self, tenant_id: str, user_id: str) -> list[dict]:
        cache_key = RedisKeys.watchlist_key(tenant_id, user_id)
        # Best-effort read-through cache (database.md §3.2): every Redis failure
        # — connection down, unparseable or non-list payload — degrades to the
        # PG query below, never to an error response.
        try:
            cached = await redis_get(cache_key)
        except Exception as e:
            logger.debug(f"Watchlist cache read failed for {cache_key}: {e}")
            cached = None

        if cached:
            try:
                payload = json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                payload = None
            if isinstance(payload, list):
                return payload
            # Corrupt entry: drop it so the next read rebuilds from PG.
            await self._invalidate_watchlist_cache(tenant_id, user_id)

        stmt = (
            select(WatchlistItem)
            .options(selectinload(WatchlistItem.symbol))
            .where(
                WatchlistItem.tenant_id == tenant_id,
                WatchlistItem.user_id == user_id,
            )
            .order_by(WatchlistItem.display_order.asc())
        )

        result = await self.db.execute(stmt)
        items = result.scalars().all()

        response = []
        for item in items:
            symbol_data = None
            if item.symbol:
                symbol_data = item.symbol

            quote_data = await self._get_cached_quote(tenant_id, symbol_data.symbol if symbol_data else "")

            response.append(
                {
                    "id": str(item.id),
                    "symbol_id": str(item.symbol_id),
                    "symbol": symbol_data.symbol if symbol_data else None,
                    "name": symbol_data.name if symbol_data else None,
                    "display_order": item.display_order,
                    "notes": item.notes,
                    "alert_threshold_percent": float(item.alert_threshold_percent)
                    if item.alert_threshold_percent
                    else None,
                    "current_price": quote_data.get("current_price") if quote_data else None,
                    "change": quote_data.get("change") if quote_data else None,
                    "change_percent": quote_data.get("change_percent") if quote_data else None,
                }
            )

        try:
            await redis_set(cache_key, json.dumps(response), ex=REDIS_TTL_WATCHLIST)
        except Exception as e:
            logger.debug(f"Watchlist cache write failed for {cache_key}: {e}")

        return response

    async def add_to_watchlist(self, tenant_id: str, user_id: str, data: dict) -> dict:
        symbol_id = data.get("symbol_id")
        if not symbol_id:
            symbol_text = (data.get("symbol") or "").strip()
            stmt_lookup = select(FinanceSymbol).where(
                FinanceSymbol.tenant_id == tenant_id,
                func.upper(FinanceSymbol.symbol) == symbol_text.upper(),
            )
            lookup_result = await self.db.execute(stmt_lookup)
            fin_symbol = lookup_result.scalar_one_or_none()
            if not fin_symbol:
                # Fund-code fallback (M2 phase A): registered/seeded fund
                # symbols carry exchange suffixes ("510300.SS") or bare codes
                # ("110011"); accept either spelling of a 6-digit fund code.
                fund_code = self._normalize_fund_code(symbol_text)
                if fund_code:
                    variants = [fund_code, f"{fund_code}.SS", f"{fund_code}.SZ", f"{fund_code}.OF"]
                    stmt_variants = (
                        select(FinanceSymbol)
                        .where(
                            FinanceSymbol.tenant_id == tenant_id,
                            FinanceSymbol.type == "fund",
                            func.upper(FinanceSymbol.symbol).in_([v.upper() for v in variants]),
                        )
                        .order_by(FinanceSymbol.symbol)
                        .limit(1)
                    )
                    fin_symbol = (await self.db.execute(stmt_variants)).scalar_one_or_none()
                    if not fin_symbol:
                        # Selection-time registration (§9.3): registry text/pinyin
                        # candidates are returned by search WITHOUT pre-registering
                        # a symbol row, so create it now (authoritative name) —
                        # this is also the plain-code entry point. Registry
                        # unavailable or code unknown → SymbolNotFound as before.
                        registry = await fund_registry.get_registry()
                        entry = registry.lookup_code(fund_code) if registry is not None else None
                        if entry is not None:
                            fin_symbol = await self._ensure_registry_fund_symbol(tenant_id, entry)
            if not fin_symbol:
                raise SymbolNotFound(message=f"Symbol not found: {symbol_text}")
            symbol_id = str(fin_symbol.id)

        stmt_count = (
            select(func.count())
            .select_from(WatchlistItem)
            .where(
                WatchlistItem.tenant_id == tenant_id,
                WatchlistItem.user_id == user_id,
            )
        )
        count_result = await self.db.execute(stmt_count)
        current_count = count_result.scalar() or 0

        if current_count >= MAX_WATCHLIST_ITEMS:
            raise ValidationError(
                message=f"Watchlist limit reached (max {MAX_WATCHLIST_ITEMS} items)",
                details=[{"field": "watchlist", "message": f"Maximum {MAX_WATCHLIST_ITEMS} items allowed"}],
            )

        stmt_dup = select(WatchlistItem).where(
            WatchlistItem.tenant_id == tenant_id,
            WatchlistItem.user_id == user_id,
            WatchlistItem.symbol_id == symbol_id,
        )
        dup_result = await self.db.execute(stmt_dup)
        if dup_result.scalar_one_or_none():
            raise DuplicateWatchlistItem(message=f"Symbol {symbol_id} already in watchlist")

        max_order_stmt = select(func.max(WatchlistItem.display_order)).where(
            WatchlistItem.tenant_id == tenant_id,
            WatchlistItem.user_id == user_id,
        )
        max_order_result = await self.db.execute(max_order_stmt)
        max_order = max_order_result.scalar() or -1
        next_order = max_order + 1

        new_item = WatchlistItem(
            tenant_id=tenant_id,
            user_id=user_id,
            symbol_id=symbol_id,
            display_order=data.get("display_order", next_order),
            notes=data.get("notes"),
            alert_threshold_percent=data.get("alert_threshold_percent"),
        )
        self.db.add(new_item)
        await self.db.commit()
        await self.db.refresh(new_item)

        stmt_symbol = select(FinanceSymbol).where(FinanceSymbol.id == new_item.symbol_id)
        sym_result = await self.db.execute(stmt_symbol)
        symbol_data = sym_result.scalar_one_or_none()

        quote_data = await self._get_cached_quote(tenant_id, symbol_data.symbol if symbol_data else "")

        await self._invalidate_watchlist_cache(tenant_id, user_id)

        # Holdings-ingestion hook (fund-intraday-nav.md §5.1): adding a fund to
        # the watchlist makes it part of the global followed union, so trigger a
        # fire-and-forget f10 holdings fetch (budget-gated, failures only logged)
        # and invalidate the followed-codes cache so the next estimate cycle picks
        # the fund up. Never blocks or breaks the watchlist response.
        if symbol_data is not None and symbol_data.type == "fund":
            fund_code = self._normalize_fund_code(symbol_data.symbol)
            if fund_code:
                from app.services.fund_holdings import (
                    invalidate_followed_codes_cache,
                    spawn_holdings_ingestion,
                )

                await invalidate_followed_codes_cache()
                spawn_holdings_ingestion(fund_code)

        return {
            "id": str(new_item.id),
            "symbol_id": str(new_item.symbol_id),
            "symbol": symbol_data.symbol if symbol_data else None,
            "name": symbol_data.name if symbol_data else None,
            "display_order": new_item.display_order,
            "notes": new_item.notes,
            "alert_threshold_percent": float(new_item.alert_threshold_percent)
            if new_item.alert_threshold_percent
            else None,
            "current_price": quote_data.get("current_price") if quote_data else None,
            "change": quote_data.get("change") if quote_data else None,
            "change_percent": quote_data.get("change_percent") if quote_data else None,
        }

    async def remove_from_watchlist(self, tenant_id: str, user_id: str, item_id: str) -> None:
        stmt = select(WatchlistItem).where(
            WatchlistItem.id == item_id,
            WatchlistItem.tenant_id == tenant_id,
            WatchlistItem.user_id == user_id,
        )
        result = await self.db.execute(stmt)
        item = result.scalar_one_or_none()

        if not item:
            raise SymbolNotFound(message=f"Watchlist item not found: {item_id}")

        await self.db.delete(item)
        await self.db.commit()

        await self._invalidate_watchlist_cache(tenant_id, user_id)
        # The followed-fund union may have shrunk; force a rebuild on next
        # estimate-cycle read (fund-intraday-nav.md §5.2).
        from app.services.fund_holdings import invalidate_followed_codes_cache

        await invalidate_followed_codes_cache()

    async def reorder_watchlist(self, tenant_id: str, user_id: str, order_items: list[dict]) -> None:
        for order_item in order_items:
            stmt = select(WatchlistItem).where(
                WatchlistItem.id == order_item["item_id"],
                WatchlistItem.tenant_id == tenant_id,
                WatchlistItem.user_id == user_id,
            )
            result = await self.db.execute(stmt)
            item = result.scalar_one_or_none()
            if item:
                item.display_order = order_item["display_order"]

        await self.db.commit()

        await self._invalidate_watchlist_cache(tenant_id, user_id)

    async def update_watchlist_alert_threshold(
        self,
        tenant_id: str,
        user_id: str,
        item_id: str,
        alert_threshold_percent: float | None,
    ) -> dict:
        """Set (or clear with null) the price-alert threshold of a watchlist item.

        A "not found" and a "belongs to another user" miss are indistinguishable
        by design (both 404 SymbolNotFound) so item existence is never leaked
        across users. finance-tab.md §3.2.
        """
        if alert_threshold_percent is not None and not (
            ALERT_THRESHOLD_MIN <= alert_threshold_percent <= ALERT_THRESHOLD_MAX
        ):
            raise ValidationError(
                message=(
                    f"alert_threshold_percent must be between {ALERT_THRESHOLD_MIN} "
                    f"and {ALERT_THRESHOLD_MAX} or null to disable"
                ),
                details=[
                    {
                        "field": "alert_threshold_percent",
                        "message": f"value must be within [{ALERT_THRESHOLD_MIN}, {ALERT_THRESHOLD_MAX}]",
                    }
                ],
            )

        stmt = select(WatchlistItem).where(
            WatchlistItem.id == item_id,
            WatchlistItem.tenant_id == tenant_id,
            WatchlistItem.user_id == user_id,
        )
        result = await self.db.execute(stmt)
        item = result.scalar_one_or_none()

        if not item:
            raise SymbolNotFound(message=f"Watchlist item not found: {item_id}")

        item.alert_threshold_percent = alert_threshold_percent
        await self.db.commit()
        await self.db.refresh(item)

        await self._invalidate_watchlist_cache(tenant_id, user_id)

        stmt_symbol = select(FinanceSymbol).where(FinanceSymbol.id == item.symbol_id)
        sym_result = await self.db.execute(stmt_symbol)
        symbol_data = sym_result.scalar_one_or_none()

        return {
            "id": str(item.id),
            "symbol_id": str(item.symbol_id),
            "symbol": symbol_data.symbol if symbol_data else None,
            "name": symbol_data.name if symbol_data else None,
            "display_order": item.display_order,
            "notes": item.notes,
            "alert_threshold_percent": float(item.alert_threshold_percent) if item.alert_threshold_percent else None,
            "current_price": None,
            "change": None,
            "change_percent": None,
        }

    async def _check_alert_threshold(self, tenant_id: str, item: dict, quote: dict) -> None:
        """Publish an alert_update when the entry's threshold is breached.

        Runs piggyback on the watchlist quote path (no market-wide scanner).
        Guarded by an atomic SET NX EX cooldown marker per item so a
        persistently-breached threshold fires at most once per
        ALERT_FIRED_TTL window. Redis unavailable → skip detection silently
        (no alert, no error); the push itself falls back to the in-process
        direct push inside event_router.push_event like every other event.
        finance-tab.md §3.2.
        """
        threshold = item.get("alert_threshold_percent")
        change_percent = quote.get("change_percent")
        if threshold is None or change_percent is None:
            return

        if abs(float(change_percent)) < float(threshold):
            return

        cooldown_key = RedisKeys.alert_fired_key(tenant_id, item.get("id", ""))
        try:
            client = await get_redis_client()
            acquired = await client.set(
                cooldown_key,
                "1",
                ex=RedisKeys.ALERT_FIRED_TTL,
                nx=True,
            )
        except Exception as e:
            logger.debug(f"Alert cooldown check failed for watchlist item {item.get('id')}: {e}")
            return

        if not acquired:
            return

        payload = {
            "symbol": item.get("symbol"),
            "name": item.get("name") or quote.get("name"),
            "price": quote.get("current_price"),
            "change_percent": change_percent,
            "threshold_percent": threshold,
            "direction": "up" if float(change_percent) >= 0 else "down",
            "triggered_at": datetime.now(UTC).isoformat(),
        }
        await event_router.push_event(
            "finance",
            SSEEventType.ALERT_UPDATE,
            payload,
            tenant_id,
        )

    async def get_watchlist_quotes(self, tenant_id: str, user_id: str) -> list[dict]:
        watchlist = await self.get_watchlist(tenant_id, user_id)
        quotes = []
        for item in watchlist:
            symbol = item.get("symbol")
            if symbol:
                quote = await self._get_cached_quote(tenant_id, symbol)
                if not quote:
                    try:
                        quote = await self.get_quote(tenant_id, symbol)
                    except SymbolNotFound:
                        quote = None
                if quote:
                    quotes.append(quote)
                    # Alert detection rides on the quote path: checked for every
                    # resolved quote, de-duplicated by the Redis cooldown marker.
                    await self._check_alert_threshold(tenant_id, item, quote)

        # Fund rows carry the intraday NAV estimate when the worker loop has
        # one in fund_nav_rt (fund-intraday-nav.md §9.1); non-fund rows keep
        # fund_nav=None. The dicts here are fresh json.loads copies (quote
        # cache / fetch path), so attaching the field never corrupts a cache.
        symbol_types = await self._quote_symbols_types(tenant_id, [q.get("symbol") for q in quotes])
        for quote in quotes:
            symbol = quote.get("symbol")
            fund_nav = None
            if symbol and symbol_types.get(symbol) == "fund":
                fund_code = self._normalize_fund_code(symbol)
                if fund_code:
                    fund_nav = await self._read_fund_nav_rt(fund_code)
            quote["fund_nav"] = fund_nav
        return quotes

    def _is_market_open(self, market: str) -> bool:
        tz = MARKET_TIMEZONES.get(market)
        if not tz:
            return False

        now = datetime.now(tz)
        # Holiday calendar first: an exchange holiday closes the whole day
        # regardless of the trading window (finance-tab.md §3.4.4).
        if is_market_holiday(market, now.date()):
            return False
        if now.weekday() >= 5:
            return False

        trading_hours = MARKET_TRADING_HOURS.get(market, [])
        now_time = now.time()

        return any(open_time <= now_time <= close_time for open_time, close_time in trading_hours)

    def get_market_status(self, market: str) -> dict:
        """Open/closed status with the closed reason and holiday name.

        Returns {"status": "open"|"closed", "reason": "weekend"|"holiday"|"off_hours"|None,
        "holiday_name": str (holiday only)}. finance-tab.md §3.4.4.
        """
        tz = MARKET_TIMEZONES.get(market)
        if tz is None:
            return {"status": "closed", "reason": None}

        now = datetime.now(tz)
        reason = market_closed_reason(market, now)
        status: dict = {"status": "open" if reason is None else "closed", "reason": reason}
        if reason == CLOSED_REASON_HOLIDAY:
            status["holiday_name"] = get_market_holiday_name(market, now.date())
        return status

    def is_any_market_open(self) -> bool:
        """True while at least one major market is in a trading window (weekdays only).

        Gate signal for the periodic market refresh jobs (finance-tab.md §3.8.2): the job
        body skips silently when every market is closed, saving external API calls. This
        replaces the originally designed market_indices_off_hours low-frequency job.
        """
        return any(self._is_market_open(region) for region in MARKET_TIMEZONES)

    async def _get_cached_quote(self, tenant_id: str, symbol: str) -> dict | None:
        cache_key = RedisKeys.quote_key(tenant_id, symbol)
        try:
            cached = await redis_get(cache_key)
        except Exception as e:
            # Redis down degrades to "no cached quote" instead of failing the
            # caller (e.g. the get_watchlist fallback path, database.md §3.2).
            logger.debug(f"Quote cache read failed for {cache_key}: {e}")
            return None
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    async def _cache_quote(self, tenant_id: str, symbol: str, quote_data: dict) -> None:
        cache_key = RedisKeys.quote_key(tenant_id, symbol)
        try:
            await redis_set(cache_key, json.dumps(quote_data), ex=REDIS_TTL_QUOTE)
        except Exception as e:
            # A write-back failure must not discard an already-fetched quote.
            logger.debug(f"Quote cache write failed for {cache_key}: {e}")

    async def _store_quote_to_db(self, tenant_id: str, symbol: str, quote_data: dict) -> None:
        stmt = select(FinanceSymbol).where(
            FinanceSymbol.tenant_id == tenant_id,
            FinanceSymbol.symbol == symbol,
        )
        result = await self.db.execute(stmt)
        fin_symbol = result.scalar_one_or_none()

        if not fin_symbol:
            fin_symbol = FinanceSymbol(
                tenant_id=tenant_id,
                symbol=symbol,
                name=quote_data.get("name", symbol),
                type=quote_data.get("type", "stock"),
                market=quote_data.get("market", self._infer_market(symbol)),
                exchange=quote_data.get("exchange", ""),
                currency=quote_data.get("currency", "USD"),
                is_active=True,
            )
            self.db.add(fin_symbol)
            await self.db.flush()

        new_quote = FinanceQuote(
            tenant_id=tenant_id,
            symbol_id=fin_symbol.id,
            current_price=quote_data.get("current_price"),
            open_price=quote_data.get("open"),
            high_price=quote_data.get("high"),
            low_price=quote_data.get("low"),
            close_previous=quote_data.get("close_previous") or quote_data.get("previous_close"),
            volume=quote_data.get("volume"),
            change_value=quote_data.get("change"),
            change_percent=quote_data.get("change_percent"),
            market_cap=quote_data.get("market_cap"),
            pe_ratio=quote_data.get("pe_ratio"),
            week_high_52=quote_data.get("52_week_high") or quote_data.get("week_high_52"),
            week_low_52=quote_data.get("52_week_low") or quote_data.get("week_low_52"),
            timestamp=datetime.now(UTC),
            source_name=quote_data.get("source", "unknown"),
        )
        self.db.add(new_quote)
        await self.db.commit()

    async def _fetch_quote_with_failover(self, tenant_id: str, symbol: str) -> dict | None:
        failover_chain = self._get_failover_chain(DATA_TYPE_STOCK_QUOTE, symbol)
        return await self._fetch_with_failover(tenant_id, symbol, failover_chain)

    async def _fetch_indices_with_failover(self, tenant_id: str) -> list[dict] | None:
        symbols = [c["symbol"] for c in MARKET_INDICES_CONFIG]
        failover_chain = self._get_failover_chain(DATA_TYPE_MARKET_INDICES)

        # eastmoney only covers A-share indices (a domestic source, reachable from staging),
        # while yfinance covers everything but gets rate-limited. Fill in progressively along
        # the chain and merge by symbol instead of "first non-empty result wins"; otherwise
        # all non-CN indices would be missing whenever eastmoney succeeds.
        merged: dict[str, dict] = {}
        for source_config in failover_chain:
            missing = [s for s in symbols if s not in merged]
            if not missing:
                break
            try:
                results = await self._try_collector_batch(tenant_id, missing, source_config)
            except Exception as e:
                logger.warning(f"Failover: {source_config['name']} failed for market indices: {e}")
                continue
            if results:
                for item in results:
                    item_symbol = item.get("symbol")
                    if item_symbol and item_symbol not in merged:
                        merged[item_symbol] = item

        if not merged:
            logger.error(f"All failover sources failed for market indices batch {symbols[:3]}...")
            return None
        return list(merged.values())

    async def _fetch_commodities_with_failover(self, tenant_id: str) -> list[dict] | None:
        symbols = [c["symbol"] for c in COMMODITIES_CONFIG]
        failover_chain = self._get_failover_chain(DATA_TYPE_COMMODITY)
        return await self._fetch_with_failover_batch(tenant_id, symbols, failover_chain)

    async def _fetch_with_failover(self, tenant_id: str, symbol: str, failover_chain: list[dict]) -> dict | None:
        for source_config in failover_chain:
            try:
                result = await self._try_collector(tenant_id, symbol, source_config)
                if result:
                    return result
            except Exception as e:
                logger.warning(f"Failover: {source_config['name']} failed for {symbol}: {e}")
                continue

        logger.error(f"All failover sources failed for symbol {symbol}")
        return None

    async def _fetch_with_failover_batch(
        self, tenant_id: str, symbols: list[str], failover_chain: list[dict]
    ) -> list[dict] | None:
        for source_config in failover_chain:
            try:
                results = await self._try_collector_batch(tenant_id, symbols, source_config)
                if results and len(results) > 0:
                    return results
            except Exception as e:
                logger.warning(f"Failover: {source_config['name']} failed for batch {symbols[:3]}...: {e}")
                continue

        logger.error(f"All failover sources failed for batch {symbols[:3]}...")
        return None

    def _get_failover_chain(self, data_type: str, symbol: str | None = None) -> list[dict]:
        if data_type == DATA_TYPE_STOCK_QUOTE:
            is_cn = symbol and (
                symbol.endswith(".SS") or symbol.endswith(".SZ") or symbol.startswith("0") or symbol.startswith("3")
            )
            if is_cn:
                return [
                    {"name": "eastmoney", "collector": "eastmoney"},
                    {"name": "yfinance", "collector": "yfinance"},
                ]
            return [
                {"name": "yfinance", "collector": "yfinance"},
                {"name": "alpha_vantage", "collector": "alpha_vantage"},
                {"name": "finnhub", "collector": "finnhub"},
            ]

        if data_type == DATA_TYPE_MARKET_INDICES:
            # Fix the spelling mismatch (the old branch matched "market_index" and never hit).
            # Indices chain eastmoney→yfinance: eastmoney covers A-share indices (domestic
            # source, reachable from staging) and yfinance is the full-coverage fallback;
            # works together with the merge-by-symbol logic in _fetch_indices_with_failover.
            return [
                {"name": "eastmoney", "collector": "eastmoney"},
                {"name": "yfinance", "collector": "yfinance"},
            ]

        if data_type == DATA_TYPE_COMMODITY:
            return [
                {"name": "yfinance", "collector": "yfinance"},
                {"name": "alpha_vantage", "collector": "alpha_vantage"},
            ]

        return [{"name": "yfinance", "collector": "yfinance"}]

    async def _try_collector(self, tenant_id: str, symbol: str, source_config: dict) -> dict | None:
        from app.collectors import COLLECTOR_REGISTRY

        collector_class = COLLECTOR_REGISTRY.get(source_config["collector"])
        if not collector_class:
            return None

        collector = collector_class()

        mock_source = _MockSource(
            tenant_id=tenant_id,
            name=source_config["name"],
            config={"symbols": [symbol]},
        )

        result = await collector.collect(mock_source)
        if result.success and result.items:
            for item in result.items:
                if item.get("symbol") == symbol:
                    item["source"] = source_config["name"]
                    return item
            if result.items:
                return result.items[0]

        return None

    async def _try_collector_batch(self, tenant_id: str, symbols: list[str], source_config: dict) -> list[dict] | None:
        from app.collectors import COLLECTOR_REGISTRY

        collector_class = COLLECTOR_REGISTRY.get(source_config["collector"])
        if not collector_class:
            return None

        collector = collector_class()

        if source_config["collector"] == "eastmoney":
            mock_source = _MockSource(
                tenant_id=tenant_id,
                name=source_config["name"],
                config={"symbols": symbols, "data_type": "cn_indices"},
            )
        else:
            mock_source = _MockSource(
                tenant_id=tenant_id,
                name=source_config["name"],
                config={"symbols": symbols},
            )

        result = await collector.collect(mock_source)
        if result.success and result.items:
            items = result.items
            if source_config["collector"] == "eastmoney":
                # Adaptation: eastmoney returns secid-style symbols (e.g. "1.000001"); map
                # them back to standard symbols ("000001.SS") so callers can match them
                # against MARKET_INDICES_CONFIG. Indices outside the requested list are dropped.
                from app.collectors.finance.eastmoney_collector import EastMoneyCollector

                converter = EastMoneyCollector()
                symbol_map = {s: s for s in symbols}
                symbol_map.update({converter._convert_symbol_to_secid(s): s for s in symbols})
                items = [
                    {**item, "symbol": symbol_map[item["symbol"]]} for item in items if item.get("symbol") in symbol_map
                ]
            for item in items:
                item["source"] = source_config["name"]
            return items

        return None

    def _registry_candidate_result(self, entry: fund_registry.FundRegistryEntry) -> dict:
        """Search-result row for a registry text/pinyin candidate. The symbol is
        NOT pre-registered — registration happens at selection time
        (add_to_watchlist's registry fallback, §9.3)."""
        return {
            "symbol": entry.code,
            "name": entry.name,
            "type": "fund",
            "market": "CN",
            "exchange": "",
            "current_price": None,
            "change_percent": None,
            "currency": "CNY",
        }

    async def _register_registry_fund(self, tenant_id: str, entry: fund_registry.FundRegistryEntry) -> dict:
        """Ensure a type='fund' symbol exists under the tenant for an
        authoritative registry entry (real name, not the legacy placeholder)
        and return its search-result row."""
        symbol = await self._ensure_registry_fund_symbol(tenant_id, entry)
        return {
            "symbol": symbol.symbol,
            "name": symbol.name,
            "type": symbol.type,
            "market": symbol.market,
            "exchange": symbol.exchange or "",
            "current_price": None,
            "change_percent": None,
            "currency": symbol.currency or "CNY",
        }

    async def _ensure_registry_fund_symbol(
        self, tenant_id: str, entry: fund_registry.FundRegistryEntry
    ) -> FinanceSymbol:
        """Reuse an existing spelling variant under the tenant or create the
        bare-code fund symbol; legacy placeholder names heal to the registry
        name (§9.3)."""
        variants = [entry.code, f"{entry.code}.SS", f"{entry.code}.SZ", f"{entry.code}.OF"]
        existing_stmt = (
            select(FinanceSymbol)
            .where(
                FinanceSymbol.tenant_id == tenant_id,
                func.upper(FinanceSymbol.symbol).in_([v.upper() for v in variants]),
            )
            .order_by(FinanceSymbol.symbol)
            .limit(1)
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            if existing.type == "fund" and fund_registry.is_placeholder_name(existing.name, entry.code):
                existing.name = entry.name
                try:
                    await self.db.commit()
                except Exception as e:
                    logger.warning(f"Fund placeholder-name healing failed for {entry.code}: {e}")
                    await self.db.rollback()
            return existing

        new_symbol = FinanceSymbol(
            tenant_id=tenant_id,
            symbol=entry.code,
            name=entry.name,
            type="fund",
            market="CN",
            exchange="",
            currency="CNY",
            is_active=True,
        )
        self.db.add(new_symbol)
        try:
            await self.db.commit()
            return new_symbol
        except Exception as e:
            # Concurrent search racing the unique constraint: the winner's row is
            # re-looked up; never fail the request over the race.
            logger.warning(f"Fund registry symbol registration failed for {entry.code}: {e}")
            await self.db.rollback()
            winner = (await self.db.execute(existing_stmt)).scalar_one_or_none()
            return winner if winner is not None else new_symbol

    async def _maybe_autoregister_fund_code(self, tenant_id: str, query: str) -> dict | None:
        """Register a bare 6-digit CN fund code as a fund symbol (§M2 phase A).

        Fires only when search produced no hits at all — with the fund registry
        loaded (§9.3) exact codes resolve there first with authoritative names;
        this heuristic path (placeholder name, prefix-classified codes) is the
        degradation target for registry outages. Listed ETF/LOF codes normally
        resolve through the external path (with the corrected type). Returns
        the search-result dict for the (existing or newly created) symbol, or
        None when the query is not a fund code / already registered.
        """
        fund_code = self._normalize_fund_code(query)
        if not fund_code or not self._is_cn_fund_code(fund_code):
            return None

        # Already registered under this tenant (bare code or a suffixed
        # variant)? Reuse it instead of duplicating.
        variants = [fund_code, f"{fund_code}.SS", f"{fund_code}.SZ", f"{fund_code}.OF"]
        existing_stmt = (
            select(FinanceSymbol)
            .where(
                FinanceSymbol.tenant_id == tenant_id,
                func.upper(FinanceSymbol.symbol).in_([v.upper() for v in variants]),
            )
            .order_by(FinanceSymbol.symbol)
            .limit(1)
        )
        existing = (await self.db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            return {
                "symbol": existing.symbol,
                "name": existing.name,
                "type": existing.type,
                "market": existing.market,
                "exchange": existing.exchange,
                "current_price": None,
                "change_percent": None,
                "currency": existing.currency or "CNY",
            }

        new_symbol = FinanceSymbol(
            tenant_id=tenant_id,
            symbol=fund_code,
            name=fund_registry.fund_placeholder_name(fund_code),
            type="fund",
            market="CN",
            exchange="",
            currency="CNY",
            is_active=True,
        )
        self.db.add(new_symbol)
        try:
            await self.db.commit()
        except Exception as e:
            # Concurrent search races the unique constraint; the winner's row is
            # picked up by the next search — never fail this request over it.
            logger.warning(f"Fund code auto-registration failed for {fund_code}: {e}")
            await self.db.rollback()
        return {
            "symbol": fund_code,
            "name": fund_registry.fund_placeholder_name(fund_code),
            "type": "fund",
            "market": "CN",
            "exchange": "",
            "current_price": None,
            "change_percent": None,
            "currency": "CNY",
        }

    async def _search_symbols_external(self, query: str, tenant_id: str) -> list[dict]:
        try:
            from app.collectors.finance.yfinance_collector import YFinanceCollector

            YFinanceCollector()
            search_url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}&quotesCount=10&newsCount=0"

            import httpx

            async with httpx.AsyncClient(timeout=10) as client:
                # Yahoo rate-limits default client fingerprints (429); send browser-like
                # headers to improve the success rate.
                response = await client.get(search_url, headers=YAHOO_BROWSER_HEADERS)
                if response.status_code == 429:
                    logger.warning("Yahoo search rate-limited (429), returning empty results")
                    return []
                if response.status_code != 200:
                    return []

                data = response.json()
                quotes = data.get("quotes", [])

                results = []
                for q in quotes:
                    symbol = q.get("symbol", "")
                    name = q.get("shortname", "") or q.get("longname", "") or symbol
                    q_type = q.get("quoteType", "stock")
                    exchange = q.get("exchange", "")

                    if not symbol:
                        continue

                    market = self._infer_market(symbol)
                    # CN listed funds are upstream-reported as EQUITY; the
                    # fund-code heuristic restores type='fund' (M2 phase A).
                    symbol_type = self._classify_symbol_type(symbol, q_type)

                    fin_symbol = FinanceSymbol(
                        tenant_id=tenant_id,
                        symbol=symbol,
                        name=name,
                        type=symbol_type,
                        market=market,
                        exchange=exchange,
                        currency=q.get("currency", "USD"),
                        is_active=True,
                    )
                    self.db.add(fin_symbol)

                    results.append(
                        {
                            "symbol": symbol,
                            "name": name,
                            "type": symbol_type,
                            "market": market,
                            "exchange": exchange,
                            "current_price": None,
                            "change_percent": None,
                            "currency": q.get("currency", "USD"),
                        }
                    )

                if results:
                    await self.db.commit()

                return results
        except Exception as e:
            logger.warning(f"External symbol search failed for '{query}': {e}")
            return []

    def _infer_market(self, symbol: str) -> str:
        if symbol.endswith(".SS") or symbol.endswith(".SZ"):
            return "CN"
        if symbol.endswith(".HK"):
            return "HK"
        if symbol.endswith(".T") or symbol.endswith(".JP"):
            return "JP"
        if symbol.endswith(".L") or symbol.endswith(".GB"):
            return "GB"
        if symbol.endswith(".DE") or symbol.endswith(".F"):
            return "DE"
        if symbol.startswith("^"):
            return "US"
        return "US"

    @classmethod
    def _map_yfinance_type(cls, q_type: str) -> str:
        type_map = {
            "EQUITY": "stock",
            "ETF": "fund",
            "MUTUALFUND": "fund",
            "INDEX": "index",
            "CURRENCY": "currency",
            "CRYPTOCURRENCY": "currency",
            "FUTURE": "futures",
            "COMMODITY": "commodity",
        }
        return type_map.get(q_type, "stock")

    # 6-digit CN fund-code heuristic (fund-intraday-nav.md, M2 phase A).
    # DEMOTED to fallback once the fund registry (§9.3) is loaded — the registry
    # is the authoritative catalog and resolves any registered code including the
    # families this heuristic cannot see. The heuristic remains the degradation
    # path for registry outages.
    # External search indexes map CN listed ETFs/LOFs to EQUITY and miss OTC
    # open-end funds entirely, which left the intraday NAV pipeline without
    # consumable type='fund' symbols. Unambiguous CN fund-code families:
    #   leading '5'           — SSE listed-fund family (ETF/LOF/close-end)
    #   prefixes 15 / 16 / 18 — SZSE listed-fund family (ETF/LOF/close-end)
    #   prefixes 005..009     — mainstream OTC open-end fund ranges
    # Main-board / GEM / STAR / BSE equity prefixes (60/68/00/30/8x/4x/9x)
    # never classify as fund. OTC 11x-series funds (convertible-bond code
    # collision) are deliberately NOT covered here — they reach the pipeline
    # via the seed data instead.
    _CN_FUND_CODE_LEADING = "5"
    _CN_FUND_CODE_PREFIXES2 = ("15", "16", "18")
    _CN_FUND_CODE_PREFIXES3 = ("005", "006", "007", "008", "009")

    @classmethod
    def _is_cn_fund_code(cls, code: str | None) -> bool:
        if not code or len(code) != 6 or not code.isdigit():
            return False
        return (
            code[0] == cls._CN_FUND_CODE_LEADING
            or code[:2] in cls._CN_FUND_CODE_PREFIXES2
            or code[:3] in cls._CN_FUND_CODE_PREFIXES3
        )

    @classmethod
    def _classify_symbol_type(cls, symbol: str, yahoo_quote_type: str) -> str:
        """Effective symbol type: the CN fund-code heuristic overrides Yahoo's
        mapping (CN listed funds are upstream-reported as EQUITY)."""
        fund_code = cls._normalize_fund_code(symbol)
        if fund_code and cls._is_cn_fund_code(fund_code):
            return "fund"
        return cls._map_yfinance_type(yahoo_quote_type)

    def _paginate_results(self, results: list, page: int, page_size: int) -> dict:
        total = len(results)
        start = (page - 1) * page_size
        end = start + page_size
        paginated = results[start:end]
        return {
            "data": paginated,
            "meta": {
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }


class _MockSource:
    def __init__(self, tenant_id: str, name: str, config: dict = None):
        self.id = "mock"
        self.tenant_id = tenant_id
        self.name = name
        self.config = config or {}
        self.url = ""
        self.source_type = "api"
