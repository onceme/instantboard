import hashlib
import json
import logging
from datetime import UTC, datetime
from datetime import time as dt_time
from zoneinfo import ZoneInfo

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
from app.core.redis import RedisKeys, redis_delete, redis_get, redis_set
from app.core.sse_router import SSEEventType, event_router
from app.models.finance import FinanceQuote, FinanceSymbol, FundNAVEstimate
from app.models.watchlist import WatchlistItem

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

MARKET_TIMEZONES = {
    "US": ZoneInfo("America/New_York"),
    "CN": ZoneInfo("Asia/Shanghai"),
    "HK": ZoneInfo("Asia/Hong_Kong"),
    "JP": ZoneInfo("Asia/Tokyo"),
    "GB": ZoneInfo("Europe/London"),
    "DE": ZoneInfo("Europe/Berlin"),
    "FR": ZoneInfo("Europe/Paris"),
    "KR": ZoneInfo("Asia/Seoul"),
    "IN": ZoneInfo("Asia/Kolkata"),
}

MARKET_TRADING_HOURS = {
    "US": [(dt_time(9, 30), dt_time(16, 0))],
    "CN": [(dt_time(9, 30), dt_time(11, 30)), (dt_time(13, 0), dt_time(15, 0))],
    "HK": [(dt_time(9, 30), dt_time(16, 0))],
    "JP": [(dt_time(9, 0), dt_time(15, 0))],
    "GB": [(dt_time(8, 0), dt_time(16, 30))],
    "DE": [(dt_time(9, 0), dt_time(17, 30))],
    "FR": [(dt_time(9, 0), dt_time(17, 30))],
    "KR": [(dt_time(9, 0), dt_time(15, 30))],
    "IN": [(dt_time(9, 15), dt_time(15, 30))],
}

REDIS_TTL_QUOTE = 30
REDIS_TTL_MARKET_INDEX = 60
REDIS_TTL_COMMODITY = 60
REDIS_TTL_NAV = 120
REDIS_TTL_SEARCH = 300

MAX_WATCHLIST_ITEMS = 512

# Failover data-type identifiers, promoted to module-level constants to prevent a recurrence
# of the "market_indices"/"market_index" spelling mismatch (previously
# _fetch_indices_with_failover passed "market_indices" while the chain matched
# "market_index", so the indices chain always fell through to the fallback).
DATA_TYPE_STOCK_QUOTE = "stock_quote"
DATA_TYPE_MARKET_INDICES = "market_indices"
DATA_TYPE_COMMODITY = "commodity"


class FinanceService:
    def __init__(self, db: AsyncSession, redis: Redis):
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

        for stmt_level in [stmt_exact, stmt_prefix, stmt_fuzzy]:
            rows = await self.db.execute(stmt_level)
            for row in rows.scalars().all():
                if row.symbol not in seen_symbols:
                    seen_symbols.add(row.symbol)
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

        if not results:
            external_results = await self._search_symbols_external(q, tenant_id)
            for ext in external_results:
                if ext["symbol"] not in seen_symbols:
                    seen_symbols.add(ext["symbol"])
                    results.append(ext)

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

        results = await self._fetch_indices_with_failover(tenant_id)

        if not results:
            raise ServiceUnavailable(message="Market indices data temporarily unavailable")

        formatted = []
        for config in MARKET_INDICES_CONFIG:
            idx_data = None
            for r in results:
                if r.get("symbol") == config["symbol"]:
                    idx_data = r
                    break

            market_status = self._is_market_open(config["region"])

            formatted.append(
                {
                    "symbol": config["symbol"],
                    "name": config["name"],
                    "value": idx_data.get("current_price") if idx_data else None,
                    "change": idx_data.get("change") if idx_data else None,
                    "change_percent": idx_data.get("change_percent") if idx_data else None,
                    "market_status": "open" if market_status else "closed",
                    "region": config["region"],
                    "timestamp": idx_data.get("timestamp") if idx_data else None,
                }
            )

        await redis_set(cache_key, json.dumps(formatted), ex=REDIS_TTL_MARKET_INDEX)

        await event_router.push_event(
            "finance",
            SSEEventType.MARKET_INDEX_UPDATE,
            formatted,
            tenant_id,
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

        results = await self._fetch_commodities_with_failover(tenant_id)

        if not results:
            raise ServiceUnavailable(message="Commodity data temporarily unavailable")

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

        await redis_set(cache_key, json.dumps(formatted), ex=REDIS_TTL_COMMODITY)

        await event_router.push_event(
            "finance",
            SSEEventType.COMMODITY_UPDATE,
            formatted,
            tenant_id,
        )

        return formatted

    async def get_fund_nav(self, tenant_id: str, symbol: str, estimate_type: str = "realtime") -> dict:
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
            raise SymbolNotFound(message=f"Fund symbol not found: {symbol}")

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
        nav_estimate = None
        nav_estimate_deviation_percent = None
        estimate_method = None
        underlying_index_info = None

        if nav_record:
            nav_official = float(nav_record.nav_official) if nav_record.nav_official else None
            nav_official_date = str(nav_record.nav_official_date) if nav_record.nav_official_date else None
            nav_estimate = float(nav_record.nav_estimate) if nav_record.nav_estimate else None
            nav_estimate_deviation_percent = (
                float(nav_record.nav_estimate_deviation_percent) if nav_record.nav_estimate_deviation_percent else None
            )
            estimate_method = nav_record.estimate_method

            if nav_record.underlying_index_symbol:
                index_quote = await self._get_cached_quote(tenant_id, nav_record.underlying_index_symbol)
                underlying_index_info = {
                    "symbol": nav_record.underlying_index_symbol,
                    "name": nav_record.underlying_index_symbol,
                    "current_value": float(nav_record.underlying_index_value)
                    if nav_record.underlying_index_value
                    else None,
                    "change_percent": float(nav_record.underlying_index_change_percent)
                    if nav_record.underlying_index_change_percent
                    else None,
                }
                if index_quote:
                    underlying_index_info["name"] = index_quote.get("name", nav_record.underlying_index_symbol)
                    underlying_index_info["current_value"] = index_quote.get("current_price")
                    underlying_index_info["change_percent"] = index_quote.get("change_percent")

        if estimate_type == "realtime" and fund.type == "fund" and nav_official and underlying_index_info:
            index_change = underlying_index_info.get("change_percent", 0) or 0
            tracking_ratio = 1.0
            nav_estimate = nav_official * (1 + index_change / 100 * tracking_ratio)
            nav_estimate_deviation_percent = (
                round((nav_estimate - nav_official) / nav_official * 100, 4) if nav_official else None
            )
            estimate_method = "index_tracking"

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

    async def get_watchlist(self, tenant_id: str, user_id: str) -> list[dict]:
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

        watchlist_cache_key = RedisKeys.watchlist_key(tenant_id, user_id)
        await redis_delete(watchlist_cache_key)

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

        watchlist_cache_key = RedisKeys.watchlist_key(tenant_id, user_id)
        await redis_delete(watchlist_cache_key)

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

        watchlist_cache_key = RedisKeys.watchlist_key(tenant_id, user_id)
        await redis_delete(watchlist_cache_key)

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
        return quotes

    def _is_market_open(self, market: str) -> bool:
        tz = MARKET_TIMEZONES.get(market)
        if not tz:
            return False

        now = datetime.now(tz)
        if now.weekday() >= 5:
            return False

        trading_hours = MARKET_TRADING_HOURS.get(market, [])
        now_time = now.time()

        return any(open_time <= now_time <= close_time for open_time, close_time in trading_hours)

    async def _get_cached_quote(self, tenant_id: str, symbol: str) -> dict | None:
        cache_key = RedisKeys.quote_key(tenant_id, symbol)
        cached = await redis_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass
        return None

    async def _cache_quote(self, tenant_id: str, symbol: str, quote_data: dict) -> None:
        cache_key = RedisKeys.quote_key(tenant_id, symbol)
        await redis_set(cache_key, json.dumps(quote_data), ex=REDIS_TTL_QUOTE)

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

                    fin_symbol = FinanceSymbol(
                        tenant_id=tenant_id,
                        symbol=symbol,
                        name=name,
                        type=self._map_yfinance_type(q_type),
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
                            "type": self._map_yfinance_type(q_type),
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

    def _map_yfinance_type(self, q_type: str) -> str:
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
