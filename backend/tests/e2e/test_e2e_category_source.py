"""E2E: category & data-source management journey.

Create a custom category -> bind an RSS source -> category detail lists the source
-> deleting the category while it still owns sources is rejected (400) -> delete
source -> category deletion succeeds (204).
"""

import uuid

import pytest

from tests.e2e.conftest import bearer, make_e2e_token

pytestmark = pytest.mark.e2e


class TestCategorySourceManagement:
    async def test_category_source_lifecycle(self, aclient, e2e_tenant_and_user):
        headers = bearer(make_e2e_token(e2e_tenant_and_user["tenant_id"], e2e_tenant_and_user["user_id"]))
        suffix = uuid.uuid4().hex[:8]

        resp = await aclient.post(
            "/api/v1/categories",
            headers=headers,
            json={
                "name": f"E2E Custom Feed {suffix}",
                "type": "custom",
                "description": "E2E managed board",
                "refresh_interval_seconds": 300,
            },
        )
        assert resp.status_code == 201
        category = resp.json()["data"]
        category_id = category["id"]
        assert category["slug"] == f"e2e-custom-feed-{suffix}"
        assert category["source_count"] == 0

        resp = await aclient.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "name": "E2E RSS Source",
                "category_id": category_id,
                "source_type": "rss",
                "url": "https://e2e.example/rss.xml",
                "config": {"url": "https://e2e.example/rss.xml"},
            },
        )
        assert resp.status_code == 201
        source = resp.json()["data"]
        source_id = source["id"]
        assert source["category_id"] == category_id
        assert source["is_active"] is True
        assert source["collector_available"] is True
        assert source["health_status"] == "healthy"

        resp = await aclient.get(f"/api/v1/categories/{category_id}/sources", headers=headers)
        assert resp.status_code == 200
        detail = resp.json()["data"]
        assert detail["source_count"] == 1
        assert detail["sources"][0]["id"] == source_id
        assert detail["sources"][0]["source_type"] == "rss"

        # Category still owns a source -> deletion must be rejected
        resp = await aclient.delete(f"/api/v1/categories/{category_id}", headers=headers)
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

        resp = await aclient.delete(f"/api/v1/sources/{source_id}", headers=headers)
        assert resp.status_code == 204

        resp = await aclient.delete(f"/api/v1/categories/{category_id}", headers=headers)
        assert resp.status_code == 204

        resp = await aclient.get(f"/api/v1/categories/{category_id}", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "CATEGORY_NOT_FOUND"

    async def test_active_source_without_collector_rejected(self, aclient, e2e_tenant_and_user):
        headers = bearer(make_e2e_token(e2e_tenant_and_user["tenant_id"], e2e_tenant_and_user["user_id"]))
        suffix = uuid.uuid4().hex[:8]

        resp = await aclient.post(
            "/api/v1/categories",
            headers=headers,
            json={"name": f"E2E NoCollector {suffix}", "type": "custom"},
        )
        assert resp.status_code == 201
        category_id = resp.json()["data"]["id"]

        # web_scrape has no collector of its own and config.library names none ->
        # the pre-flight check must reject the active source up front.
        resp = await aclient.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "name": "E2E Unreachable Source",
                "category_id": category_id,
                "source_type": "web_scrape",
                "url": "https://e2e.example/page",
                "config": {"url": "https://e2e.example/page", "selector": ".news"},
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "NO_COLLECTOR_AVAILABLE"

        resp = await aclient.delete(f"/api/v1/categories/{category_id}", headers=headers)
        assert resp.status_code == 204
