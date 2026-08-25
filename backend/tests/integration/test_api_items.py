"""API-level tests for the item tag endpoints (POST/DELETE /api/v1/items/{id}/tags).

The ItemService is mocked (real-SQL behaviour lives in test_item_tags_sql.py);
these tests cover routing, auth wiring, status codes and the error envelope.
"""

import uuid
from unittest.mock import AsyncMock, patch

from app.core.exceptions import ItemNotFound, ValidationError
from app.schemas.base import SuccessResponse
from app.schemas.item import ItemTagsResponse
from tests.integration.conftest import make_auth_header


def _tags_response(item_id, topic_tags):
    return SuccessResponse(
        success=True,
        data=ItemTagsResponse(item_id=item_id, topic_tags=topic_tags),
    )


class TestAddItemTag:
    @patch("app.api.v1.items._get_item_service")
    def test_add_tag_success(self, mock_svc_fn, client):
        headers, tenant_id, _ = make_auth_header()
        item_id = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.add_tag.return_value = _tags_response(item_id, ["tech", "ai", "my-note"])
        mock_svc_fn.return_value = mock_svc

        resp = client.post(f"/api/v1/items/{item_id}/tags", headers=headers, json={"tag": "my-note"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["item_id"] == item_id
        assert body["data"]["topic_tags"] == ["tech", "ai", "my-note"]
        mock_svc.add_tag.assert_called_once_with(item_id=item_id, tag="my-note", tenant_id=tenant_id)

    def test_add_tag_no_auth(self, client):
        resp = client.post(f"/api/v1/items/{uuid.uuid4()}/tags", json={"tag": "x"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    def test_add_tag_missing_body_field(self, client):
        headers, _, _ = make_auth_header()
        resp = client.post(f"/api/v1/items/{uuid.uuid4()}/tags", headers=headers, json={})
        assert resp.status_code == 422

    @patch("app.api.v1.items._get_item_service")
    def test_add_tag_invalid_format(self, mock_svc_fn, client):
        headers, _, _ = make_auth_header()
        mock_svc = AsyncMock()
        mock_svc.add_tag.side_effect = ValidationError(
            message="Invalid tag format", details=[{"field": "tag", "message": "bad"}]
        )
        mock_svc_fn.return_value = mock_svc

        resp = client.post(f"/api/v1/items/{uuid.uuid4()}/tags", headers=headers, json={"tag": "Bad Tag"})

        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == "tag"

    @patch("app.api.v1.items._get_item_service")
    def test_add_tag_limit_reached(self, mock_svc_fn, client):
        headers, _, _ = make_auth_header()
        mock_svc = AsyncMock()
        mock_svc.add_tag.side_effect = ValidationError(message="Tag limit reached (max 20 per item)")
        mock_svc_fn.return_value = mock_svc

        resp = client.post(f"/api/v1/items/{uuid.uuid4()}/tags", headers=headers, json={"tag": "one-more"})

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    @patch("app.api.v1.items._get_item_service")
    def test_add_tag_item_not_found(self, mock_svc_fn, client):
        headers, _, _ = make_auth_header()
        mock_svc = AsyncMock()
        mock_svc.add_tag.side_effect = ItemNotFound()
        mock_svc_fn.return_value = mock_svc

        resp = client.post(f"/api/v1/items/{uuid.uuid4()}/tags", headers=headers, json={"tag": "x"})

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "ITEM_NOT_FOUND"

    @patch("app.api.v1.items._get_item_service")
    def test_add_tag_cross_tenant_returns_404(self, mock_svc_fn, client):
        headers, _, _ = make_auth_header()
        mock_svc = AsyncMock()
        mock_svc.add_tag.side_effect = ItemNotFound(message="Item not accessible for this tenant")
        mock_svc_fn.return_value = mock_svc

        resp = client.post(f"/api/v1/items/{uuid.uuid4()}/tags", headers=headers, json={"tag": "x"})

        assert resp.status_code == 404
        error = resp.json()["detail"]["error"]
        assert error["code"] == "ITEM_NOT_FOUND"
        assert error["message"] == "Item not accessible for this tenant"


class TestRemoveItemTag:
    @patch("app.api.v1.items._get_item_service")
    def test_remove_tag_success(self, mock_svc_fn, client):
        headers, tenant_id, _ = make_auth_header()
        item_id = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.remove_tag.return_value = _tags_response(item_id, ["tech", "llm"])
        mock_svc_fn.return_value = mock_svc

        resp = client.delete(f"/api/v1/items/{item_id}/tags/ai", headers=headers)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["topic_tags"] == ["tech", "llm"]
        mock_svc.remove_tag.assert_called_once_with(item_id=item_id, tag="ai", tenant_id=tenant_id)

    def test_remove_tag_no_auth(self, client):
        resp = client.delete(f"/api/v1/items/{uuid.uuid4()}/tags/ai")
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "AUTH_REQUIRED"

    @patch("app.api.v1.items._get_item_service")
    def test_remove_tag_not_on_item(self, mock_svc_fn, client):
        headers, _, _ = make_auth_header()
        mock_svc = AsyncMock()
        mock_svc.remove_tag.side_effect = ValidationError(message="Tag not found on this item")
        mock_svc_fn.return_value = mock_svc

        resp = client.delete(f"/api/v1/items/{uuid.uuid4()}/tags/missing", headers=headers)

        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    @patch("app.api.v1.items._get_item_service")
    def test_remove_tag_item_not_found(self, mock_svc_fn, client):
        headers, _, _ = make_auth_header()
        mock_svc = AsyncMock()
        mock_svc.remove_tag.side_effect = ItemNotFound()
        mock_svc_fn.return_value = mock_svc

        resp = client.delete(f"/api/v1/items/{uuid.uuid4()}/tags/x", headers=headers)

        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "ITEM_NOT_FOUND"
