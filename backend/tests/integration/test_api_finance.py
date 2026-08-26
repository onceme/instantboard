"""Tests for /api/v1/finance endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.api.v1.finance import _get_finance_service
from app.core.exceptions import SymbolNotFound, ValidationError
from app.core.security import create_access_token

NOW = datetime.now(UTC).isoformat()


def _token(role="admin", tenant_id=None):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "role": role,
            "provider": "github",
            "type": "access",
        }
    )


def _headers(tid=None):
    return {"Authorization": f"Bearer {_token(tenant_id=tid)}"}


@pytest.fixture
def mock_finance_svc(app_with_overrides):
    app, _ = app_with_overrides
    mock_svc = AsyncMock()

    async def override():
        return mock_svc

    app.dependency_overrides[_get_finance_service] = override
    yield mock_svc
    app.dependency_overrides.pop(_get_finance_service, None)


class TestFinanceSearch:
    def test_search_success(self, client, mock_finance_svc):
        mock_finance_svc.search_symbols.return_value = {
            "data": [
                {
                    "symbol": "AAPL",
                    "name": "Apple Inc",
                    "type": "stock",
                    "market": "US",
                    "exchange": "NASDAQ",
                    "current_price": 180.0,
                    "change_percent": 1.5,
                    "currency": "USD",
                },
            ],
            "meta": {"total": 1, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/finance/search?q=AAPL", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"][0]["symbol"] == "AAPL"

    def test_search_with_type_market(self, client, mock_finance_svc):
        mock_finance_svc.search_symbols.return_value = {"data": [], "meta": {"total": 0, "page": 1, "page_size": 20}}

        resp = client.get("/api/v1/finance/search?q=apple&type=stock&market=US", headers=_headers())
        assert resp.status_code == 200

    def test_search_missing_query(self, client, mock_finance_svc):
        resp = client.get("/api/v1/finance/search", headers=_headers())
        assert resp.status_code == 422


class TestFinanceQuote:
    def test_quote_success(self, client, mock_finance_svc):
        mock_finance_svc.get_quote.return_value = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "current_price": 180.0,
            "open": 178.0,
            "high": 182.0,
            "low": 177.0,
            "close_previous": 179.0,
            "volume": 1000000,
            "change": 1.0,
            "change_percent": 0.56,
            "market_cap": 2800000000000,
            "pe_ratio": 28.5,
            "week_high_52": 200.0,
            "week_low_52": 150.0,
            "timestamp": NOW,
            "source": "yfinance",
        }

        resp = client.get("/api/v1/finance/quote/AAPL", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["symbol"] == "AAPL"
        assert data["current_price"] == 180.0

    def test_quote_detail_level(self, client, mock_finance_svc):
        mock_finance_svc.get_quote.return_value = {
            "symbol": "AAPL",
            "name": "Apple",
            "current_price": 180.0,
            "timestamp": NOW,
        }

        resp = client.get("/api/v1/finance/quote/AAPL?detail_level=full", headers=_headers())
        assert resp.status_code == 200

    def test_quote_with_52_week_keys(self, client, mock_finance_svc):
        mock_finance_svc.get_quote.return_value = {
            "symbol": "TSLA",
            "name": "Tesla",
            "current_price": 250.0,
            "52_week_high": 300.0,
            "52_week_low": 150.0,
            "timestamp": NOW,
        }

        resp = client.get("/api/v1/finance/quote/TSLA", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["week_high_52"] == 300.0
        assert data["week_low_52"] == 150.0

    def test_quote_with_previous_close_key(self, client, mock_finance_svc):
        mock_finance_svc.get_quote.return_value = {
            "symbol": "MSFT",
            "name": "Microsoft",
            "current_price": 400.0,
            "previous_close": 398.0,
            "timestamp": NOW,
        }

        resp = client.get("/api/v1/finance/quote/MSFT", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["close_previous"] == 398.0

    def test_quote_with_history(self, client, mock_finance_svc):
        mock_finance_svc.get_quote.return_value = {
            "symbol": "AAPL",
            "name": "Apple Inc",
            "current_price": 180.0,
            "timestamp": NOW,
            "history": [
                {"time": "2026-08-20T20:00:00Z", "close": 178.5},
                {"time": "2026-08-21T20:00:00Z", "close": 180.0},
            ],
        }

        resp = client.get("/api/v1/finance/quote/AAPL", headers=_headers())
        assert resp.status_code == 200
        history = resp.json()["data"]["history"]
        assert history == [
            {"time": "2026-08-20T20:00:00Z", "close": 178.5},
            {"time": "2026-08-21T20:00:00Z", "close": 180.0},
        ]

    def test_quote_without_history_defaults_empty(self, client, mock_finance_svc):
        # Sources without chart data (alpha_vantage/finnhub failover) must
        # degrade to an empty series, never a missing key or an error.
        mock_finance_svc.get_quote.return_value = {
            "symbol": "MSFT",
            "name": "Microsoft",
            "current_price": 400.0,
            "timestamp": NOW,
        }

        resp = client.get("/api/v1/finance/quote/MSFT", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["history"] == []


class TestMarketIndices:
    def test_indices_success(self, client, mock_finance_svc):
        mock_finance_svc.get_market_indices.return_value = [
            {
                "symbol": "^GSPC",
                "name": "S&P 500",
                "value": 5000.0,
                "change": 20.0,
                "change_percent": 0.4,
                "market_status": "open",
                "region": "US",
                "timestamp": NOW,
            },
        ]

        resp = client.get("/api/v1/finance/market-indices", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"][0]["symbol"] == "^GSPC"

    def test_indices_with_string_timestamp(self, client, mock_finance_svc):
        mock_finance_svc.get_market_indices.return_value = [
            {
                "symbol": "000001.SS",
                "name": "上证综指",
                "value": 3200.0,
                "change": -10.0,
                "change_percent": -0.3,
                "market_status": "closed",
                "region": "CN",
                "timestamp": "2024-01-15T03:00:00Z",
            },
        ]

        resp = client.get("/api/v1/finance/market-indices", headers=_headers())
        assert resp.status_code == 200

    def test_indices_invalid_timestamp(self, client, mock_finance_svc):
        mock_finance_svc.get_market_indices.return_value = [
            {
                "symbol": "^DJI",
                "name": "Dow Jones",
                "value": 38000.0,
                "change": 100.0,
                "change_percent": 0.26,
                "market_status": "open",
                "region": "US",
                "timestamp": "not-a-timestamp",
            },
        ]

        resp = client.get("/api/v1/finance/market-indices", headers=_headers())
        assert resp.status_code == 200


class TestCommodities:
    def test_commodities_success(self, client, mock_finance_svc):
        mock_finance_svc.get_commodities.return_value = [
            {
                "symbol": "GC=F",
                "name": "Gold",
                "value": 2050.0,
                "change": 10.0,
                "change_percent": 0.49,
                "unit": "USD/oz",
                "timestamp": NOW,
            },
        ]

        resp = client.get("/api/v1/finance/commodities", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"][0]["symbol"] == "GC=F"

    def test_commodities_string_timestamp(self, client, mock_finance_svc):
        mock_finance_svc.get_commodities.return_value = [
            {
                "symbol": "SI=F",
                "name": "Silver",
                "value": 25.0,
                "change": 0.5,
                "change_percent": 2.0,
                "unit": "USD/oz",
                "timestamp": "2024-01-15T10:00:00Z",
            },
        ]

        resp = client.get("/api/v1/finance/commodities", headers=_headers())
        assert resp.status_code == 200

    def test_commodities_invalid_timestamp(self, client, mock_finance_svc):
        mock_finance_svc.get_commodities.return_value = [
            {
                "symbol": "CL=F",
                "name": "Crude Oil",
                "value": 75.0,
                "change": -1.0,
                "change_percent": -1.3,
                "unit": "USD/bbl",
                "timestamp": "garbage",
            },
        ]

        resp = client.get("/api/v1/finance/commodities", headers=_headers())
        assert resp.status_code == 200


class TestFundNAV:
    def test_fund_nav_success(self, client, mock_finance_svc):
        mock_finance_svc.get_fund_nav.return_value = {
            "symbol": "510300",
            "name": "华泰柏瑞沪深300ETF",
            "nav_official": 3.85,
            "nav_official_date": "2024-01-15",
            "nav_estimate": 3.86,
            "nav_estimate_deviation_percent": 0.26,
            "estimate_method": "realtime_index",
            "estimate_timestamp": NOW,
            "underlying_index": {
                "symbol": "000300",
                "name": "沪深300",
                "current_value": 3350.0,
                "change_percent": 0.3,
            },
        }

        resp = client.get("/api/v1/finance/fund/510300/nav", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["nav_estimate"] == 3.86
        assert data["underlying_index"]["symbol"] == "000300"

    def test_fund_nav_no_underlying(self, client, mock_finance_svc):
        mock_finance_svc.get_fund_nav.return_value = {
            "symbol": "159915",
            "name": "创业板ETF",
            "nav_estimate": 1.5,
            "estimate_method": "T-1",
        }

        resp = client.get("/api/v1/finance/fund/159915/nav", headers=_headers())
        assert resp.status_code == 200

    def test_fund_nav_string_timestamp(self, client, mock_finance_svc):
        mock_finance_svc.get_fund_nav.return_value = {
            "symbol": "510050",
            "name": "上证50ETF",
            "nav_estimate": 2.8,
            "estimate_timestamp": "2024-01-15T14:30:00Z",
        }

        resp = client.get("/api/v1/finance/fund/510050/nav", headers=_headers())
        assert resp.status_code == 200

    def test_fund_nav_invalid_timestamp(self, client, mock_finance_svc):
        mock_finance_svc.get_fund_nav.return_value = {
            "symbol": "510500",
            "name": "中证500ETF",
            "nav_estimate": 5.5,
            "estimate_timestamp": "bad-timestamp",
        }

        resp = client.get("/api/v1/finance/fund/510500/nav", headers=_headers())
        assert resp.status_code == 200

    def test_fund_nav_estimate_type_param(self, client, mock_finance_svc):
        mock_finance_svc.get_fund_nav.return_value = {
            "symbol": "510300",
            "name": "沪深300ETF",
            "nav_estimate": 3.86,
        }

        resp = client.get("/api/v1/finance/fund/510300/nav?estimate_type=t-1", headers=_headers())
        assert resp.status_code == 200


class TestWatchlistCRUD:
    def test_get_watchlist(self, client, mock_finance_svc):
        mock_finance_svc.get_watchlist.return_value = [
            {
                "id": str(uuid.uuid4()),
                "symbol_id": "AAPL",
                "symbol": "AAPL",
                "name": "Apple",
                "display_order": 0,
                "notes": None,
                "alert_threshold_percent": None,
                "current_price": 180.0,
                "change": 1.0,
                "change_percent": 0.56,
            },
        ]

        resp = client.get("/api/v1/finance/watchlist", headers=_headers())
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    def test_add_to_watchlist(self, client, mock_finance_svc):
        wid = str(uuid.uuid4())
        mock_finance_svc.add_to_watchlist.return_value = {
            "id": wid,
            "symbol_id": "AAPL",
            "symbol": "AAPL",
            "name": "Apple",
            "display_order": 0,
            "notes": None,
            "alert_threshold_percent": None,
            "current_price": None,
            "change": None,
            "change_percent": None,
        }

        resp = client.post(
            "/api/v1/finance/watchlist",
            headers=_headers(),
            json={"symbol_id": "AAPL", "display_order": 0},
        )
        assert resp.status_code == 201

    def test_remove_from_watchlist(self, client, mock_finance_svc):
        mock_finance_svc.remove_from_watchlist.return_value = None
        item_id = str(uuid.uuid4())

        resp = client.delete(f"/api/v1/finance/watchlist/{item_id}", headers=_headers())
        assert resp.status_code == 204


class TestWatchlistReorder:
    def test_reorder_success(self, client, mock_finance_svc):
        mock_finance_svc.reorder_watchlist.return_value = None
        items = [
            {"item_id": str(uuid.uuid4()), "display_order": 0},
            {"item_id": str(uuid.uuid4()), "display_order": 1},
        ]

        resp = client.put(
            "/api/v1/finance/watchlist/reorder",
            headers=_headers(),
            json={"items": items},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["message"] == "Watchlist order updated"


class TestWatchlistQuotes:
    def test_watchlist_quotes_success(self, client, mock_finance_svc):
        mock_finance_svc.get_watchlist_quotes.return_value = [
            {
                "symbol": "AAPL",
                "name": "Apple",
                "current_price": 180.0,
                "change": 1.0,
                "change_percent": 0.56,
                "week_high_52": 200.0,
                "week_low_52": 150.0,
                "timestamp": NOW,
            },
        ]

        resp = client.get("/api/v1/finance/watchlist/quotes", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"][0]["symbol"] == "AAPL"

    def test_watchlist_quotes_previous_close(self, client, mock_finance_svc):
        mock_finance_svc.get_watchlist_quotes.return_value = [
            {
                "symbol": "MSFT",
                "name": "Microsoft",
                "current_price": 400.0,
                "previous_close": 398.0,
                "52_week_high": 420.0,
                "52_week_low": 350.0,
                "timestamp": NOW,
            },
        ]

        resp = client.get("/api/v1/finance/watchlist/quotes", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()["data"][0]
        assert data["close_previous"] == 398.0
        assert data["week_high_52"] == 420.0


class TestWatchlistAlertThreshold:
    def _item_payload(self, item_id, threshold):
        return {
            "id": item_id,
            "symbol_id": str(uuid.uuid4()),
            "symbol": "AAPL",
            "name": "Apple",
            "display_order": 0,
            "notes": None,
            "alert_threshold_percent": threshold,
            "current_price": None,
            "change": None,
            "change_percent": None,
        }

    def test_update_threshold_success(self, client, mock_finance_svc):
        item_id = str(uuid.uuid4())
        mock_finance_svc.update_watchlist_alert_threshold.return_value = self._item_payload(item_id, 2.5)

        resp = client.patch(
            f"/api/v1/finance/watchlist/{item_id}",
            headers=_headers(),
            json={"alert_threshold_percent": 2.5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["id"] == item_id
        assert body["data"]["alert_threshold_percent"] == 2.5

        mock_finance_svc.update_watchlist_alert_threshold.assert_called_once()
        kwargs = mock_finance_svc.update_watchlist_alert_threshold.call_args.kwargs
        assert kwargs["item_id"] == item_id
        assert kwargs["alert_threshold_percent"] == 2.5

    def test_update_threshold_null_disables(self, client, mock_finance_svc):
        item_id = str(uuid.uuid4())
        mock_finance_svc.update_watchlist_alert_threshold.return_value = self._item_payload(item_id, None)

        resp = client.patch(
            f"/api/v1/finance/watchlist/{item_id}",
            headers=_headers(),
            json={"alert_threshold_percent": None},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["alert_threshold_percent"] is None
        kwargs = mock_finance_svc.update_watchlist_alert_threshold.call_args.kwargs
        assert kwargs["alert_threshold_percent"] is None

    def test_update_threshold_out_of_range_400(self, client, mock_finance_svc):
        mock_finance_svc.update_watchlist_alert_threshold.side_effect = ValidationError(
            message="alert_threshold_percent must be between 0.5 and 50 or null to disable"
        )

        resp = client.patch(
            f"/api/v1/finance/watchlist/{uuid.uuid4()}",
            headers=_headers(),
            json={"alert_threshold_percent": 0.1},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    def test_update_threshold_not_found_or_not_owned_404(self, client, mock_finance_svc):
        # Missing items and items owned by another user are indistinguishable
        mock_finance_svc.update_watchlist_alert_threshold.side_effect = SymbolNotFound(
            message="Watchlist item not found"
        )

        resp = client.patch(
            f"/api/v1/finance/watchlist/{uuid.uuid4()}",
            headers=_headers(),
            json={"alert_threshold_percent": 3},
        )
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "SYMBOL_NOT_FOUND"

    def test_update_threshold_missing_body_defaults_null(self, client, mock_finance_svc):
        item_id = str(uuid.uuid4())
        mock_finance_svc.update_watchlist_alert_threshold.return_value = self._item_payload(item_id, None)

        resp = client.patch(f"/api/v1/finance/watchlist/{item_id}", headers=_headers(), json={})
        assert resp.status_code == 200
        kwargs = mock_finance_svc.update_watchlist_alert_threshold.call_args.kwargs
        assert kwargs["alert_threshold_percent"] is None

    def test_update_threshold_non_numeric_422(self, client, mock_finance_svc):
        resp = client.patch(
            f"/api/v1/finance/watchlist/{uuid.uuid4()}",
            headers=_headers(),
            json={"alert_threshold_percent": "abc"},
        )
        assert resp.status_code == 422
        mock_finance_svc.update_watchlist_alert_threshold.assert_not_called()

    def test_update_threshold_requires_auth(self, client, mock_finance_svc):
        resp = client.patch(
            f"/api/v1/finance/watchlist/{uuid.uuid4()}",
            json={"alert_threshold_percent": 2},
        )
        assert resp.status_code == 401
