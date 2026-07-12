"""Tests for /api/v1/sources endpoints."""
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from app.core.security import create_access_token

NOW = datetime.now(UTC).isoformat()


def _token(role="admin", tenant_id=None):
    return create_access_token({
        "sub": str(uuid.uuid4()),
        "tenant_id": tenant_id or str(uuid.uuid4()),
        "role": role,
        "provider": "github",
        "type": "access",
    })


def _headers(tid=None):
    return {"Authorization": f"Bearer {_token(tenant_id=tid)}"}


def _source_response(**overrides):
    sid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    base = {
        "id": sid,
        "name": "Test RSS Source",
        "category_id": cid,
        "source_type": "rss",
        "url": "https://example.com/feed",
        "config": {},
        "refresh_interval_seconds": 300,
        "is_active": True,
        "priority": 3,
        "health_status": "healthy",
        "last_fetch_at": None,
        "last_error": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return base


class TestListSources:
    @patch("app.api.v1.sources._get_source_service")
    def test_list_success(self, mock_svc_fn, client):
        mock_svc = AsyncMock()
        mock_svc.list_sources.return_value = {
            "success": True,
            "data": [_source_response()],
            "meta": {"total": 1, "page": 1, "page_size": 20},
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get("/api/v1/sources", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["meta"]["total"] == 1

    @patch("app.api.v1.sources._get_source_service")
    def test_list_with_filters(self, mock_svc_fn, client):
        mock_svc = AsyncMock()
        mock_svc.list_sources.return_value = {
            "success": True, "data": [], "meta": {"total": 0, "page": 1, "page_size": 20},
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get(
            "/api/v1/sources?category_id=x&source_type=rss&status=healthy&is_active=true&page=1&page_size=5",
            headers=_headers(),
        )
        assert resp.status_code == 200

    def test_list_no_auth(self, client):
        resp = client.get("/api/v1/sources")
        assert resp.status_code == 401


class TestCreateSource:
    @patch("app.api.v1.sources._get_source_service")
    def test_create_success(self, mock_svc_fn, client):
        mock_svc = AsyncMock()
        cid = str(uuid.uuid4())
        mock_svc.create_source.return_value = {
            "success": True,
            "data": _source_response(category_id=cid),
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.post(
            "/api/v1/sources",
            headers=_headers(),
            json={
                "name": "New Source",
                "category_id": cid,
                "source_type": "rss",
                "url": "https://example.com/feed",
            },
        )
        assert resp.status_code == 201

    def test_create_invalid_source_type(self, client):
        resp = client.post(
            "/api/v1/sources",
            headers=_headers(),
            json={
                "name": "Bad",
                "category_id": str(uuid.uuid4()),
                "source_type": "ftp",
                "url": "http://x",
            },
        )
        assert resp.status_code == 422


class TestGetSource:
    @patch("app.api.v1.sources._get_source_service")
    def test_get_found(self, mock_svc_fn, client):
        sid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.get_source.return_value = {"success": True, "data": _source_response(id=sid)}
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/sources/{sid}", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["id"] == sid

    @patch("app.api.v1.sources._get_source_service")
    def test_get_not_found(self, mock_svc_fn, client):
        from app.core.exceptions import SourceNotFound
        mock_svc = AsyncMock()
        mock_svc.get_source.side_effect = SourceNotFound()
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/sources/{uuid.uuid4()}", headers=_headers())
        assert resp.status_code == 404


class TestUpdateSource:
    @patch("app.api.v1.sources._get_source_service")
    def test_update_success(self, mock_svc_fn, client):
        sid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.update_source.return_value = {
            "success": True, "data": _source_response(id=sid, name="Updated"),
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.put(f"/api/v1/sources/{sid}", headers=_headers(), json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Updated"


class TestDeleteSource:
    @patch("app.api.v1.sources._get_source_service")
    def test_delete_success(self, mock_svc_fn, client):
        sid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.delete_source.return_value = None
        mock_svc_fn.return_value = mock_svc

        resp = client.delete(f"/api/v1/sources/{sid}", headers=_headers())
        assert resp.status_code == 204


class TestSourceHealth:
    @patch("app.api.v1.sources._get_source_service")
    def test_health_success(self, mock_svc_fn, client):
        sid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.get_source_health.return_value = {
            "success": True,
            "data": {
                "source_id": sid,
                "status": "healthy",
                "success_rate_24h": 99.5,
                "avg_response_time_ms": 150,
                "last_success_at": NOW,
                "last_failure_at": None,
                "consecutive_failures": 0,
                "total_fetches_24h": 100,
                "last_error": None,
            },
        }
        mock_svc_fn.return_value = mock_svc

        resp = client.get(f"/api/v1/sources/{sid}/health", headers=_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "healthy"
