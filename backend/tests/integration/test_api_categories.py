"""Tests for /api/v1/categories endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from tests.integration.conftest import make_auth_header

NOW = datetime.now(UTC).isoformat()


def _cat_response(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "name": "Test Category",
        "slug": "test-category",
        "description": None,
        "icon": "folder",
        "color": "#3B82F6",
        "type": "tech",
        "refresh_interval_seconds": 300,
        "is_active": True,
        "source_count": 0,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return base


class TestListCategories:
    @patch("app.api.v1.categories._get_category_service")
    def test_list_categories_success(self, mock_svc_fn, client):
        tenant_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {_token(tenant_id)}"}

        mock_svc = AsyncMock()
        mock_svc.list_categories.return_value = {
            "success": True,
            "data": [_cat_response()],
            "meta": {"total": 1, "page": 1, "page_size": 20},
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get("/api/v1/categories", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 1

    @patch("app.api.v1.categories._get_category_service")
    def test_list_categories_with_type_filter(self, mock_svc_fn, client):
        tenant_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {_token(tenant_id)}"}
        mock_svc = AsyncMock()
        mock_svc.list_categories.return_value = {
            "success": True,
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get("/api/v1/categories?type=finance&page=2&page_size=10", headers=headers)
        assert resp.status_code == 200


class TestCreateCategory:
    @patch("app.api.v1.categories._get_category_service")
    def test_create_success(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        mock_svc = AsyncMock()
        mock_svc.create_category.return_value = {
            "success": True,
            "data": _cat_response(name="New Cat", slug="new-cat"),
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.post(
            "/api/v1/categories",
            headers=headers,
            json={"name": "New Cat", "type": "tech", "refresh_interval_seconds": 300},
        )
        assert resp.status_code == 201

    def test_create_validation_error_bad_type(self, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        resp = client.post(
            "/api/v1/categories",
            headers=headers,
            json={"name": "Bad", "type": "invalid_type"},
        )
        assert resp.status_code == 422

    def test_create_no_auth(self, client):
        resp = client.post("/api/v1/categories", json={"name": "X", "type": "tech"})
        assert resp.status_code == 401


class TestPredefinedCategories:
    @patch("app.api.v1.categories._get_category_service")
    def test_get_predefined(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        mock_svc = AsyncMock()
        mock_svc.get_predefined_categories.return_value = {
            "success": True,
            "data": [_cat_response(name="Finance", type="finance")],
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get("/api/v1/categories/predefined", headers=headers)
        assert resp.status_code == 200


class TestGetCategory:
    @patch("app.api.v1.categories._get_category_service")
    def test_get_found(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.get_category.return_value = {"success": True, "data": _cat_response(id=cid)}
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{cid}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == cid

    @patch("app.api.v1.categories._get_category_service")
    def test_get_not_found(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        from app.core.exceptions import CategoryNotFound

        mock_svc = AsyncMock()
        mock_svc.get_category.side_effect = CategoryNotFound()
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{uuid.uuid4()}", headers=headers)
        assert resp.status_code == 404


class TestUpdateCategory:
    @patch("app.api.v1.categories._get_category_service")
    def test_update_success(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.update_category.return_value = {
            "success": True,
            "data": _cat_response(id=cid, name="Updated"),
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.put(f"/api/v1/categories/{cid}", headers=headers, json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Updated"


class TestDeleteCategory:
    @patch("app.api.v1.categories._get_category_service")
    def test_delete_success(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.delete_category.return_value = None
        mock_svc_fn.return_value = mock_svc

        resp = client.delete(f"/api/v1/categories/{cid}", headers=headers)
        assert resp.status_code == 204


class TestCategoryWithSources:
    @patch("app.api.v1.categories._get_category_service")
    def test_get_category_with_sources(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.get_category_with_sources.return_value = {
            "success": True,
            "data": {
                "id": cid,
                "name": "Tech",
                "slug": "tech",
                "description": None,
                "icon": "cpu",
                "color": "#3B82F6",
                "type": "tech",
                "refresh_interval_seconds": 300,
                "is_active": True,
                "source_count": 1,
                "sources": [{"id": str(uuid.uuid4()), "name": "RSS Source"}],
                "created_at": NOW,
                "updated_at": NOW,
            },
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{cid}/sources", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["sources"] is not None


class TestListSubcategories:
    @patch("app.api.v1.categories._get_category_service")
    def test_list_subcategories(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.list_subcategories.return_value = {
            "success": True,
            "data": [{"tag": "ai", "label": "AI/ML", "count": 42}],
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{cid}/subcategories", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1


def _token(tenant_id=None):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "role": "admin",
            "provider": "github",
            "type": "access",
        }
    )


# Need the import here
from app.core.security import create_access_token  # noqa: E402


def _item_response(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "title": "Test Item",
        "summary": "Summary",
        "url": "https://example.com/item",
        "source_name": "TestSource",
        "source_id": str(uuid.uuid4()),
        "category_id": str(uuid.uuid4()),
        "topic_tags": ["custom"],
        "domain_tag": None,
        "published_at": NOW,
        "fetched_at": NOW,
        "image_url": None,
        "priority": 5,
        "extra_data": {},
        "hot_score": 5.0,
    }
    base.update(overrides)
    return base


class TestListCategoryItems:
    @patch("app.api.v1.categories._get_category_service")
    def test_list_items_success_with_pagination_echo(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.list_category_items.return_value = {
            "data": [_item_response(title="First"), _item_response(title="Second")],
            "meta": {"total": 12, "page": 2, "page_size": 5},
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{cid}/items?page=2&page_size=5", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert [item["title"] for item in body["data"]] == ["First", "Second"]
        assert body["meta"] == {"total": 12, "page": 2, "page_size": 5}

    @patch("app.api.v1.categories._get_category_service")
    def test_list_items_forwards_since_sort_and_pagination(self, mock_svc_fn, client):
        tenant_id = str(uuid.uuid4())
        headers = {"Authorization": f"Bearer {_token(tenant_id)}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.list_category_items.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 3, "page_size": 10},
        }
        mock_svc_fn.return_value = mock_svc

        since = "2026-01-01T00:00:00Z"
        resp = client.get(
            f"/api/v1/categories/{cid}/items?page=3&page_size=10&sort=hot&since={since}",
            headers=headers,
        )
        assert resp.status_code == 200
        mock_svc.list_category_items.assert_awaited_once_with(
            category_id=cid,
            tenant_id=tenant_id,
            sort="hot",
            page=3,
            page_size=10,
            since=since,
        )

    @patch("app.api.v1.categories._get_category_service")
    def test_list_items_defaults_to_time_sort(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        cid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.list_category_items.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{cid}/items", headers=headers)
        assert resp.status_code == 200
        assert mock_svc.list_category_items.await_args.kwargs["sort"] == "time"
        assert mock_svc.list_category_items.await_args.kwargs["since"] is None

    @patch("app.api.v1.categories._get_category_service")
    def test_list_items_invalid_sort_rejected(self, mock_svc_fn, client):
        headers = {"Authorization": f"Bearer {_token()}"}
        mock_svc_fn.return_value = AsyncMock()

        resp = client.get(f"/api/v1/categories/{uuid.uuid4()}/items?sort=bogus", headers=headers)
        assert resp.status_code == 422

    @patch("app.api.v1.categories._get_category_service")
    def test_list_items_category_not_found(self, mock_svc_fn, client):
        from app.core.exceptions import CategoryNotFound

        headers = {"Authorization": f"Bearer {_token()}"}
        mock_svc = AsyncMock()
        mock_svc.list_category_items.side_effect = CategoryNotFound()
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{uuid.uuid4()}/items", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "CATEGORY_NOT_FOUND"

    @patch("app.api.v1.categories._get_category_service")
    def test_list_items_cross_tenant_returns_404(self, mock_svc_fn, client):
        from app.core.exceptions import CategoryNotFound

        headers = {"Authorization": f"Bearer {_token()}"}
        mock_svc = AsyncMock()
        mock_svc.list_category_items.side_effect = CategoryNotFound(message="Category not accessible for this tenant")
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/categories/{uuid.uuid4()}/items", headers=headers)
        assert resp.status_code == 404
        error = resp.json()["detail"]["error"]
        assert error["code"] == "CATEGORY_NOT_FOUND"
        assert error["message"] == "Category not accessible for this tenant"

    def test_list_items_no_auth(self, client):
        resp = client.get(f"/api/v1/categories/{uuid.uuid4()}/items")
        assert resp.status_code == 401
