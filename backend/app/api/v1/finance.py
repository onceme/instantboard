from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_current_user, get_db, get_redis
from app.schemas.base import PaginatedMeta, PaginatedResponse, SuccessResponse
from app.schemas.finance import (
    CommodityResponse,
    FinanceQuoteResponse,
    FinanceSearchResult,
    FundNAVResponse,
    MarketIndexResponse,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistReorderRequest,
)
from app.services.finance import FinanceService

router = APIRouter()


async def _get_finance_service(db: AsyncSession = Depends(get_db), redis: Redis = Depends(get_redis)) -> FinanceService:
    return FinanceService(db=db, redis=redis)


@router.get("/search", response_model=PaginatedResponse[FinanceSearchResult])
async def search_symbols(
    q: str = Query(..., min_length=1),
    type: str | None = Query(default=None),  # noqa: A002
    market: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    result = await service.search_symbols(
        tenant_id=tenant_id,
        q=q,
        type=type,
        market=market,
        page=page,
        page_size=page_size,
    )
    return PaginatedResponse(
        data=result["data"],
        meta=PaginatedMeta(**result["meta"]),
    )


@router.get("/quote/{symbol}", response_model=SuccessResponse[FinanceQuoteResponse])
async def get_quote(
    symbol: str,
    detail_level: str = Query(default="basic"),
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    quote = await service.get_quote(tenant_id=tenant_id, symbol=symbol, detail_level=detail_level)

    week_high_52 = quote.get("52_week_high") or quote.get("week_high_52")
    week_low_52 = quote.get("52_week_low") or quote.get("week_low_52")

    data = FinanceQuoteResponse(
        symbol=quote.get("symbol", symbol),
        name=quote.get("name", symbol),
        current_price=quote.get("current_price"),
        open=quote.get("open"),
        high=quote.get("high"),
        low=quote.get("low"),
        close_previous=quote.get("close_previous") or quote.get("previous_close"),
        volume=quote.get("volume"),
        change=quote.get("change"),
        change_percent=quote.get("change_percent"),
        market_cap=quote.get("market_cap"),
        pe_ratio=quote.get("pe_ratio"),
        week_high_52=week_high_52,
        week_low_52=week_low_52,
        timestamp=quote.get("timestamp"),
        source=quote.get("source"),
    )

    return SuccessResponse(data=data)


@router.get("/market-indices", response_model=SuccessResponse[list[MarketIndexResponse]])
async def get_market_indices(
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    indices = await service.get_market_indices(tenant_id=tenant_id)

    data = []
    for idx in indices:
        timestamp = idx.get("timestamp")
        if isinstance(timestamp, str):
            try:
                from datetime import datetime

                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                timestamp = None

        data.append(
            MarketIndexResponse(
                symbol=idx["symbol"],
                name=idx["name"],
                value=idx.get("value"),
                change=idx.get("change"),
                change_percent=idx.get("change_percent"),
                market_status=idx.get("market_status"),
                region=idx["region"],
                timestamp=timestamp,
            )
        )

    return SuccessResponse(data=data)


@router.get("/commodities", response_model=SuccessResponse[list[CommodityResponse]])
async def get_commodities(
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    commodities = await service.get_commodities(tenant_id=tenant_id)

    data = []
    for comm in commodities:
        timestamp = comm.get("timestamp")
        if isinstance(timestamp, str):
            try:
                from datetime import datetime

                timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                timestamp = None

        data.append(
            CommodityResponse(
                symbol=comm["symbol"],
                name=comm["name"],
                value=comm.get("value"),
                change=comm.get("change"),
                change_percent=comm.get("change_percent"),
                unit=comm.get("unit"),
                timestamp=timestamp,
            )
        )

    return SuccessResponse(data=data)


@router.get("/fund/{symbol}/nav", response_model=SuccessResponse[FundNAVResponse])
async def get_fund_nav(
    symbol: str,
    estimate_type: str = Query(default="realtime"),
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    nav = await service.get_fund_nav(tenant_id=tenant_id, symbol=symbol, estimate_type=estimate_type)

    underlying_index = nav.get("underlying_index")
    from app.schemas.finance import UnderlyingIndexInfo

    index_info = None
    if underlying_index:
        index_info = UnderlyingIndexInfo(
            symbol=underlying_index.get("symbol", ""),
            name=underlying_index.get("name", ""),
            current_value=underlying_index.get("current_value"),
            change_percent=underlying_index.get("change_percent"),
        )

    estimate_timestamp = nav.get("estimate_timestamp")
    if isinstance(estimate_timestamp, str):
        try:
            from datetime import datetime

            estimate_timestamp = datetime.fromisoformat(estimate_timestamp.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            estimate_timestamp = None

    data = FundNAVResponse(
        symbol=nav["symbol"],
        name=nav["name"],
        nav_official=nav.get("nav_official"),
        nav_official_date=nav.get("nav_official_date"),
        nav_estimate=nav.get("nav_estimate"),
        nav_estimate_deviation_percent=nav.get("nav_estimate_deviation_percent"),
        estimate_method=nav.get("estimate_method"),
        estimate_timestamp=estimate_timestamp,
        underlying_index=index_info,
    )

    return SuccessResponse(data=data)


@router.get("/watchlist", response_model=SuccessResponse[list[WatchlistItemResponse]])
async def get_watchlist(
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    user_id = user.get("user_id")
    items = await service.get_watchlist(tenant_id=tenant_id, user_id=user_id)

    data = [WatchlistItemResponse(**item) for item in items]
    return SuccessResponse(data=data)


@router.post("/watchlist", response_model=SuccessResponse[WatchlistItemResponse], status_code=201)
async def add_to_watchlist(
    request: WatchlistItemCreate,
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    user_id = user.get("user_id")
    item = await service.add_to_watchlist(
        tenant_id=tenant_id,
        user_id=user_id,
        data=request.model_dump(),
    )
    data = WatchlistItemResponse(**item)
    return SuccessResponse(data=data)


@router.delete("/watchlist/{item_id}", status_code=204)
async def remove_from_watchlist(
    item_id: str,
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    user_id = user.get("user_id")
    await service.remove_from_watchlist(
        tenant_id=tenant_id,
        user_id=user_id,
        item_id=item_id,
    )


@router.put("/watchlist/reorder")
async def reorder_watchlist(
    request: WatchlistReorderRequest,
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    user_id = user.get("user_id")
    items_data = [{"item_id": i.item_id, "display_order": i.display_order} for i in request.items]
    await service.reorder_watchlist(
        tenant_id=tenant_id,
        user_id=user_id,
        order_items=items_data,
    )
    return SuccessResponse(data={"message": "Watchlist order updated"})


@router.get("/watchlist/quotes", response_model=SuccessResponse[list[FinanceQuoteResponse]])
async def get_watchlist_quotes(
    service: FinanceService = Depends(_get_finance_service),
    user: dict = Depends(get_current_user),
    tenant_id: str = Depends(get_current_tenant),
):
    user_id = user.get("user_id")
    quotes = await service.get_watchlist_quotes(tenant_id=tenant_id, user_id=user_id)

    data = []
    for q in quotes:
        week_high_52 = q.get("52_week_high") or q.get("week_high_52")
        week_low_52 = q.get("52_week_low") or q.get("week_low_52")
        data.append(
            FinanceQuoteResponse(
                symbol=q.get("symbol", ""),
                name=q.get("name", ""),
                current_price=q.get("current_price"),
                open=q.get("open"),
                high=q.get("high"),
                low=q.get("low"),
                close_previous=q.get("close_previous") or q.get("previous_close"),
                volume=q.get("volume"),
                change=q.get("change"),
                change_percent=q.get("change_percent"),
                market_cap=q.get("market_cap"),
                pe_ratio=q.get("pe_ratio"),
                week_high_52=week_high_52,
                week_low_52=week_low_52,
                timestamp=q.get("timestamp"),
                source=q.get("source"),
            )
        )

    return SuccessResponse(data=data)
