from datetime import datetime

from pydantic import BaseModel, Field


class FinanceSearchResult(BaseModel):
    symbol: str
    name: str
    type: str
    market: str
    exchange: str | None
    current_price: float | None
    change_percent: float | None
    currency: str


class FinanceQuoteResponse(BaseModel):
    symbol: str
    name: str
    current_price: float | None
    open: float | None
    high: float | None
    low: float | None
    close_previous: float | None
    volume: int | None
    change: float | None
    change_percent: float | None
    market_cap: int | None
    pe_ratio: float | None
    week_high_52: float | None = Field(validation_alias="52_week_high")
    week_low_52: float | None = Field(validation_alias="52_week_low")
    timestamp: datetime | None
    source: str | None

    model_config = {"populate_by_name": True}


class MarketIndexResponse(BaseModel):
    symbol: str
    name: str
    value: float | None
    change: float | None
    change_percent: float | None
    market_status: str | None
    region: str
    timestamp: datetime | None


class CommodityResponse(BaseModel):
    symbol: str
    name: str
    value: float | None
    change: float | None
    change_percent: float | None
    unit: str | None
    timestamp: datetime | None


class UnderlyingIndexInfo(BaseModel):
    symbol: str
    name: str
    current_value: float | None
    change_percent: float | None


class FundNAVResponse(BaseModel):
    symbol: str
    name: str
    nav_official: float | None
    nav_official_date: str | None
    nav_estimate: float | None
    nav_estimate_deviation_percent: float | None
    estimate_method: str | None
    estimate_timestamp: datetime | None
    underlying_index: UnderlyingIndexInfo | None


class WatchlistItemCreate(BaseModel):
    symbol_id: str
    display_order: int = Field(default=0)
    notes: str | None = Field(default=None, max_length=200)
    alert_threshold_percent: float | None = Field(default=None)


class WatchlistOrderUpdate(BaseModel):
    item_id: str
    display_order: int


class WatchlistReorderRequest(BaseModel):
    items: list[WatchlistOrderUpdate]


class WatchlistItemResponse(BaseModel):
    id: str
    symbol_id: str
    symbol: str | None
    name: str | None
    display_order: int
    notes: str | None
    alert_threshold_percent: float | None
    current_price: float | None
    change: float | None
    change_percent: float | None


class QuoteDetailResponse(BaseModel):
    symbol: str
    name: str
    current_price: float | None
    open: float | None
    high: float | None
    low: float | None
    close_previous: float | None
    volume: int | None
    change: float | None
    change_percent: float | None
    market_cap: int | None
    pe_ratio: float | None
    week_high_52: float | None
    week_low_52: float | None
    timestamp: datetime | None
    source: str | None
