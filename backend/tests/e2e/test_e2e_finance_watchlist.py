"""E2E: finance watchlist journey.

Search a locally stored symbol -> add via the {symbol} contract -> visible in list
-> reorder -> delete -> empty watchlist.
"""

import uuid

import pytest
import pytest_asyncio

from app.models.finance import FinanceSymbol
from tests.conftest import test_session_factory
from tests.e2e.conftest import bearer, make_e2e_token

pytestmark = pytest.mark.e2e


@pytest_asyncio.fixture
async def finance_env(e2e_tenant_and_user):
    tenant_id = uuid.UUID(e2e_tenant_and_user["tenant_id"])
    async with test_session_factory() as session:
        session.add_all(
            [
                FinanceSymbol(
                    tenant_id=tenant_id,
                    symbol="E2ESHIP",
                    name="E2E Shipping Corp",
                    type="stock",
                    market="US",
                    exchange="NYSE",
                    currency="USD",
                    is_active=True,
                ),
                FinanceSymbol(
                    tenant_id=tenant_id,
                    symbol="E2EROBO",
                    name="E2E Robotics Inc",
                    type="stock",
                    market="US",
                    exchange="NASDAQ",
                    currency="USD",
                    is_active=True,
                ),
            ]
        )
        await session.commit()
    return e2e_tenant_and_user


class TestFinanceWatchlistJourney:
    async def test_watchlist_full_lifecycle(self, aclient, finance_env):
        headers = bearer(make_e2e_token(finance_env["tenant_id"], finance_env["user_id"]))

        resp = await aclient.get("/api/v1/finance/search", params={"q": "E2ESHIP"}, headers=headers)
        assert resp.status_code == 200
        results = resp.json()["data"]
        assert results, "locally stored symbol must be found without external fallback"
        assert results[0]["symbol"] == "E2ESHIP"
        assert results[0]["name"] == "E2E Shipping Corp"

        # {symbol} text contract (the previously fixed API contract)
        resp = await aclient.post("/api/v1/finance/watchlist", headers=headers, json={"symbol": "E2ESHIP"})
        assert resp.status_code == 201
        first = resp.json()["data"]
        first_id = first["id"]
        assert first["symbol"] == "E2ESHIP"
        assert first["display_order"] == 0

        resp = await aclient.post("/api/v1/finance/watchlist", headers=headers, json={"symbol": "E2ESHIP"})
        assert resp.status_code == 409
        assert resp.json()["detail"]["error"]["code"] == "DUPLICATE_WATCHLIST_ITEM"

        resp = await aclient.post("/api/v1/finance/watchlist", headers=headers, json={"symbol": "NOSUCH"})
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "SYMBOL_NOT_FOUND"

        resp = await aclient.post(
            "/api/v1/finance/watchlist",
            headers=headers,
            json={"symbol": "E2EROBO", "display_order": 1},
        )
        assert resp.status_code == 201
        second_id = resp.json()["data"]["id"]

        resp = await aclient.get("/api/v1/finance/watchlist", headers=headers)
        assert resp.status_code == 200
        listing = resp.json()["data"]
        assert [item["symbol"] for item in listing] == ["E2ESHIP", "E2EROBO"]
        assert [item["display_order"] for item in listing] == [0, 1]

        resp = await aclient.put(
            "/api/v1/finance/watchlist/reorder",
            headers=headers,
            json={
                "items": [
                    {"item_id": second_id, "display_order": 0},
                    {"item_id": first_id, "display_order": 1},
                ]
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["message"] == "Watchlist order updated"

        resp = await aclient.get("/api/v1/finance/watchlist", headers=headers)
        listing = resp.json()["data"]
        assert [item["symbol"] for item in listing] == ["E2EROBO", "E2ESHIP"]

        resp = await aclient.delete(f"/api/v1/finance/watchlist/{second_id}", headers=headers)
        assert resp.status_code == 204
        resp = await aclient.delete(f"/api/v1/finance/watchlist/{first_id}", headers=headers)
        assert resp.status_code == 204

        resp = await aclient.get("/api/v1/finance/watchlist", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"] == []

        resp = await aclient.delete(f"/api/v1/finance/watchlist/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404
