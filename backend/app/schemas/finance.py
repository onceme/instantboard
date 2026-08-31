from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class FinanceSearchResult(BaseModel):
    symbol: str
    name: str
    type: str
    market: str
    exchange: str | None = None
    current_price: float | None = None
    change_percent: float | None = None
    currency: str


class QuoteHistoryPoint(BaseModel):
    time: datetime
    close: float


class FundNAVIntraday(BaseModel):
    """Realtime intraday NAV estimate for one followed fund (fund-intraday-nav.md
    §3.5). Shared verbatim by the SSE nav_batch_update payload and the REST
    batch / watchlist-quotes endpoints."""

    symbol: str  # normalized 6-digit fund code
    name: str
    nav_official: float | None = None  # anchoring official NAV
    nav_official_date: str | None = None  # staleness of the anchor (§11)
    nav_estimate: float | None = None  # intraday estimate (fall-back 2 → latest official)
    # Estimated change vs. the official NAV anchor, in percent.
    estimate_change_percent: float | None = None
    estimate_method: Literal["holdings_weighted", "index_tracking", "latest_official"]
    # Precision = sum of available holding weights (0-100); the accuracy badge.
    coverage_percent: float | None = None
    holdings_report_date: str | None = None
    quote_status: Literal["realtime", "delayed", "mixed", "frozen"]
    # e.g. ["HK","US"] → the UI renders a delayed-quote annotation.
    delayed_markets: list[str] = Field(default_factory=list)
    # Report period older than the freshness threshold (§4.3).
    holdings_stale: bool = False
    estimate_timestamp: str  # UTC ISO8601
    # REST batch only (§9.1): per-code error marker so an unknown code degrades
    # to an entry instead of failing the whole array. Absent/null on SSE payloads
    # and on every resolvable code.
    error: str | None = None


class FinanceQuoteResponse(BaseModel):
    symbol: str
    name: str
    # Intraday NAV estimate for fund entries (fund-intraday-nav.md §9.1): fed
    # from the worker's fund_nav_rt cache on watchlist/quotes; always None for
    # stocks/indices. Optional → backward compatible with older clients.
    fund_nav: FundNAVIntraday | None = None
    current_price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close_previous: float | None = None
    volume: int | None = None
    change: float | None = None
    change_percent: float | None = None
    market_cap: int | None = None
    pe_ratio: float | None = None
    week_high_52: float | None = Field(default=None, validation_alias="52_week_high")
    week_low_52: float | None = Field(default=None, validation_alias="52_week_low")
    timestamp: datetime | None = None
    source: str | None = None
    # 5d daily closes for the detail-drawer sparkline (finance-tab.md §3.1);
    # empty when the source collector does not provide chart history.
    history: list[QuoteHistoryPoint] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class MarketIndexResponse(BaseModel):
    symbol: str
    name: str
    value: float | None = None
    change: float | None = None
    change_percent: float | None = None
    market_status: str | None = None
    # weekend/holiday/off_hours while closed, None while open (§3.4.4)
    market_status_reason: str | None = None
    holiday_name: str | None = None
    region: str
    timestamp: datetime | None = None


class CommodityResponse(BaseModel):
    symbol: str
    name: str
    value: float | None = None
    change: float | None = None
    change_percent: float | None = None
    unit: str | None = None
    timestamp: datetime | None = None


class UnderlyingIndexInfo(BaseModel):
    symbol: str
    name: str
    current_value: float | None = None
    change_percent: float | None = None


class FundNAVResponse(BaseModel):
    symbol: str
    name: str
    nav_official: float | None = None
    nav_official_date: str | None = None
    nav_estimate: float | None = None
    nav_estimate_deviation_percent: float | None = None
    estimate_method: str | None = None
    estimate_timestamp: datetime | None = None
    underlying_index: UnderlyingIndexInfo | None = None


class WatchlistItemCreate(BaseModel):
    symbol_id: str | None = Field(default=None)
    symbol: str | None = Field(default=None)
    display_order: int = Field(default=0)
    notes: str | None = Field(default=None, max_length=200)
    alert_threshold_percent: float | None = Field(default=None)

    @model_validator(mode="after")
    def _require_symbol_or_symbol_id(self) -> "WatchlistItemCreate":
        if not self.symbol_id and not self.symbol:
            raise ValueError("Either symbol_id or symbol must be provided")
        return self


class WatchlistOrderUpdate(BaseModel):
    item_id: str
    display_order: int


class WatchlistReorderRequest(BaseModel):
    items: list[WatchlistOrderUpdate]


class WatchlistItemAlertUpdate(BaseModel):
    # Alert threshold in percent; null clears the alert. The 0.5-50 range is
    # enforced by FinanceService.update_watchlist_alert_threshold (400
    # VALIDATION_ERROR), not here, so out-of-range values get the app error
    # envelope instead of a FastAPI 422.
    alert_threshold_percent: float | None = Field(default=None)


class WatchlistItemResponse(BaseModel):
    id: str
    symbol_id: str
    symbol: str | None = None
    name: str | None = None
    display_order: int
    notes: str | None = None
    alert_threshold_percent: float | None = None
    current_price: float | None = None
    change: float | None = None
    change_percent: float | None = None


class QuoteDetailResponse(BaseModel):
    symbol: str
    name: str
    current_price: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close_previous: float | None = None
    volume: int | None = None
    change: float | None = None
    change_percent: float | None = None
    market_cap: int | None = None
    pe_ratio: float | None = None
    week_high_52: float | None = None
    week_low_52: float | None = None
    timestamp: datetime | None = None
    source: str | None = None
