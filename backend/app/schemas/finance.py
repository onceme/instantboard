from datetime import datetime

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


class FinanceQuoteResponse(BaseModel):
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
    week_high_52: float | None = Field(default=None, validation_alias="52_week_high")
    week_low_52: float | None = Field(default=None, validation_alias="52_week_low")
    timestamp: datetime | None = None
    source: str | None = None

    model_config = {"populate_by_name": True}


class MarketIndexResponse(BaseModel):
    symbol: str
    name: str
    value: float | None = None
    change: float | None = None
    change_percent: float | None = None
    market_status: str | None = None
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
