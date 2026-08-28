"""Integration tests for OriginGuardMiddleware through the full app stack.

Exercises the real TestClient pipeline (CORS + guard + logging middlewares in
front of the router) with browser-style Origin/Referer headers, and verifies
that requests without those headers (the style every pre-existing integration
test uses) keep working unchanged.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

ALLOWED_ORIGIN = "http://allowed.example.com:3000"
ALLOWED_REFERERS = [
    "http://allowed.example.com:3000",
    "http://allowed.example.com:3000/board?id=1",
]
DISALLOWED_ORIGIN = "https://evil.example.com"
DISALLOWED_REFERERS = [
    "https://evil.example.com",
    "https://evil.example.com/phish?next=1",
]
ORIGINS = [ALLOWED_ORIGIN]

NOW = datetime.now(UTC).isoformat()


def _token(tenant_id=None):
    from app.core.security import create_access_token

    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "role": "admin",
            "provider": "github",
            "type": "access",
        }
    )


def _cat_payload():
    return {
        "id": str(uuid.uuid4()),
        "name": "Origin Guard Cat",
        "slug": "origin-guard-cat",
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


def _assert_forbidden_envelope(resp):
    assert resp.status_code == 403
    assert resp.json() == {
        "success": False,
        "error": {"code": "FORBIDDEN", "message": "Origin not allowed", "details": None},
    }


@pytest.fixture
def guarded_client(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.cors_origins", ORIGINS)
    return client


def _mock_category_service(mock_svc_fn):
    mock_svc = AsyncMock()
    mock_svc.create_category.return_value = {"success": True, "data": _cat_payload()}
    mock_svc.list_categories.return_value = {
        "success": True,
        "data": [_cat_payload()],
        "meta": {"total": 1, "page": 1, "page_size": 20},
    }
    mock_svc_fn.return_value = mock_svc
    return mock_svc


class TestOriginGuardIntegration:
    @patch("app.api.v1.categories._get_category_service")
    def test_allowed_origin_reaches_handler(self, mock_svc_fn, guarded_client):
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Origin": ALLOWED_ORIGIN}
        resp = guarded_client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "Origin Guard Cat"

    @patch("app.api.v1.categories._get_category_service")
    @pytest.mark.parametrize(
        "origin", [DISALLOWED_ORIGIN, "http://allowed.example.com", "https://allowed.example.com:3000"]
    )
    def test_disallowed_origin_403_with_valid_token(self, mock_svc_fn, guarded_client, origin):
        _mock_category_service(mock_svc_fn)
        # The Bearer token is fully valid — the guard must reject on origin alone.
        headers = {"Authorization": f"Bearer {_token()}", "Origin": origin}
        resp = guarded_client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        _assert_forbidden_envelope(resp)
        mock_svc_fn.assert_not_called()

    @patch("app.api.v1.categories._get_category_service")
    def test_no_origin_allowed_referer_passes(self, mock_svc_fn, guarded_client):
        _mock_category_service(mock_svc_fn)
        headers = {
            "Authorization": f"Bearer {_token()}",
            "Referer": ALLOWED_REFERERS[1],
        }
        resp = guarded_client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 201

    @patch("app.api.v1.categories._get_category_service")
    @pytest.mark.parametrize("referer", DISALLOWED_REFERERS)
    def test_no_origin_disallowed_referer_403(self, mock_svc_fn, guarded_client, referer):
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Referer": referer}
        resp = guarded_client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        _assert_forbidden_envelope(resp)
        mock_svc_fn.assert_not_called()

    @patch("app.api.v1.categories._get_category_service")
    @pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
    def test_other_state_changing_methods_checked(self, mock_svc_fn, guarded_client, method):
        _mock_category_service(mock_svc_fn)
        category_id = str(uuid.uuid4())
        mock_svc_fn.return_value.update_category.return_value = {"success": True, "data": _cat_payload()}
        mock_svc_fn.return_value.delete_category.return_value = {"success": True, "data": None}
        headers = {"Authorization": f"Bearer {_token()}", "Origin": DISALLOWED_ORIGIN}
        kwargs = {"headers": headers, "json": {"name": "X"}} if method != "DELETE" else {"headers": headers}
        resp = guarded_client.request(method, f"/api/v1/categories/{category_id}", **kwargs)
        _assert_forbidden_envelope(resp)

    @patch("app.api.v1.categories._get_category_service")
    def test_browser_style_headers_without_match_do_not_break_auth_flow(self, mock_svc_fn, guarded_client):
        # A disallowed origin must not leak past the guard into 401/422 paths.
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Origin": DISALLOWED_ORIGIN}
        resp = guarded_client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 403


class TestOriginGuardDoesNotAffectReads:
    @patch("app.api.v1.categories._get_category_service")
    def test_get_with_disallowed_origin_is_not_checked(self, mock_svc_fn, guarded_client):
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Origin": DISALLOWED_ORIGIN}
        resp = guarded_client.get("/api/v1/categories", headers=headers)
        assert resp.status_code == 200

    def test_health_get_untouched(self, guarded_client):
        resp = guarded_client.get("/api/v1/health", headers={"Origin": DISALLOWED_ORIGIN})
        assert resp.status_code == 200


class TestOriginGuardNoHeadersRegression:
    @patch("app.api.v1.categories._get_category_service")
    def test_post_without_origin_or_referer_passes(self, mock_svc_fn, guarded_client):
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}"}
        resp = guarded_client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 201


class TestOriginGuardWildcard:
    @patch("app.api.v1.categories._get_category_service")
    def test_wildcard_allows_any_origin(self, mock_svc_fn, client, monkeypatch):
        monkeypatch.setattr("app.config.settings.cors_origins", ["*"])
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Origin": DISALLOWED_ORIGIN}
        resp = client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 201
