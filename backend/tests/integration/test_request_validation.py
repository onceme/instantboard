"""Integration tests for RequestValidationMiddleware through the full app stack.

Exercises the real middleware chain (RequestLogging -> OriginGuard ->
RequestValidation -> CORS -> router). Requests that must carry no User-Agent
header at all are driven through the ASGI app directly, because httpx's
TestClient always injects a User-Agent ("testclient" by default) and offers no
way to remove it; everything else uses the regular TestClient.
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest

DISALLOWED_ORIGIN = "https://evil.example.com"

NOW = datetime.now(UTC).isoformat()


def _token():
    from app.core.security import create_access_token

    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "role": "admin",
            "provider": "github",
            "type": "access",
        }
    )


def _cat_payload():
    return {
        "id": str(uuid.uuid4()),
        "name": "Request Validation Cat",
        "slug": "request-validation-cat",
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


async def _raw_request(app, method, path, headers=None, body=b""):
    """Send one request through the ASGI app with exactly the given headers.

    Unlike TestClient this adds no implicit User-Agent, so it can exercise the
    missing-header rejection path.
    """
    raw_headers = [(b"host", b"testserver")]
    raw_headers += [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers or []]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }

    body_delivered = False

    async def receive():
        nonlocal body_delivered
        if not body_delivered:
            body_delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    events = []

    async def send(message):
        events.append(message)

    await app(scope, receive, send)

    start = next(event for event in events if event["type"] == "http.response.start")
    payload = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
    return start["status"], json.loads(payload) if payload else None


def _assert_ua_envelope(status, payload):
    assert status == 400
    assert payload == {
        "success": False,
        "error": {"code": "VALIDATION_ERROR", "message": "User-Agent header is required", "details": None},
    }


def _assert_body_envelope(status, payload, limit):
    assert status == 413
    assert payload == {
        "success": False,
        "error": {
            "code": "VALIDATION_ERROR",
            "message": f"Request body must not exceed {limit} bytes",
            "details": None,
        },
    }


def _mock_category_service(mock_svc_fn):
    mock_svc = AsyncMock()
    mock_svc.create_category.return_value = {"success": True, "data": _cat_payload()}
    mock_svc_fn.return_value = mock_svc
    return mock_svc


class TestUserAgentIntegration:
    @patch("app.api.v1.categories._get_category_service")
    def test_default_testclient_ua_reaches_handler(self, mock_svc_fn, client):
        # Regression: TestClient sends User-Agent "testclient", so the entire
        # pre-existing integration suite is unaffected by this middleware.
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}"}
        resp = client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "Request Validation Cat"

    @patch("app.api.v1.categories._get_category_service")
    async def test_missing_ua_rejected_400(self, mock_svc_fn, app_with_overrides):
        app, _ = app_with_overrides
        _mock_category_service(mock_svc_fn)
        status, payload = await _raw_request(app, "POST", "/api/v1/categories")
        _assert_ua_envelope(status, payload)
        mock_svc_fn.assert_not_called()

    @patch("app.api.v1.categories._get_category_service")
    @pytest.mark.parametrize("user_agent", ["", "   "])
    async def test_blank_ua_rejected_400(self, mock_svc_fn, app_with_overrides, user_agent):
        app, _ = app_with_overrides
        _mock_category_service(mock_svc_fn)
        status, payload = await _raw_request(app, "POST", "/api/v1/categories", headers=[("user-agent", user_agent)])
        _assert_ua_envelope(status, payload)
        mock_svc_fn.assert_not_called()

    @patch("app.api.v1.categories._get_category_service")
    async def test_missing_ua_rejected_on_reads_too(self, mock_svc_fn, app_with_overrides):
        app, _ = app_with_overrides
        _mock_category_service(mock_svc_fn)
        status, payload = await _raw_request(
            app,
            "GET",
            "/api/v1/categories",
            headers=[("authorization", f"Bearer {_token()}")],
        )
        _assert_ua_envelope(status, payload)
        mock_svc_fn.assert_not_called()

    async def test_health_endpoint_is_exempt(self, app_with_overrides):
        app, _ = app_with_overrides
        status, payload = await _raw_request(app, "GET", "/api/v1/health")
        assert status == 200
        assert payload["status"] == "healthy"

    @patch("app.api.v1.categories._get_category_service")
    async def test_disabled_switch_allows_missing_ua(self, mock_svc_fn, app_with_overrides, monkeypatch):
        app, _ = app_with_overrides
        monkeypatch.setattr("app.config.settings.require_user_agent", False)
        _mock_category_service(mock_svc_fn)
        status, payload = await _raw_request(
            app,
            "POST",
            "/api/v1/categories",
            headers=[("authorization", f"Bearer {_token()}"), ("content-type", "application/json")],
            body=b'{"name": "X", "type": "tech"}',
        )
        assert status == 201
        assert payload["data"]["name"] == "Request Validation Cat"

    @patch("app.api.v1.categories._get_category_service")
    async def test_origin_guard_rejects_before_request_validation(self, mock_svc_fn, app_with_overrides, monkeypatch):
        # Stack order proof: a cross-site origin is answered with the guard's
        # 403 even though the request also lacks a User-Agent.
        app, _ = app_with_overrides
        monkeypatch.setattr("app.config.settings.cors_origins", ["http://allowed.example.com"])
        _mock_category_service(mock_svc_fn)
        status, payload = await _raw_request(app, "POST", "/api/v1/categories", headers=[("origin", DISALLOWED_ORIGIN)])
        assert status == 403
        assert payload == {
            "success": False,
            "error": {"code": "FORBIDDEN", "message": "Origin not allowed", "details": None},
        }
        mock_svc_fn.assert_not_called()


class TestBodySizeIntegration:
    @patch("app.api.v1.categories._get_category_service")
    def test_oversized_body_rejected_413(self, mock_svc_fn, client):
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}"}
        # ~11KB payload exceeds the default 10KB limit; the UA is valid, so the
        # rejection must come from the body-size check alone.
        big_payload = {"name": "X", "type": "tech", "padding": "y" * 11000}
        resp = client.post("/api/v1/categories", headers=headers, json=big_payload)
        _assert_body_envelope(resp.status_code, resp.json(), limit=10 * 1024)
        mock_svc_fn.assert_not_called()

    @patch("app.api.v1.categories._get_category_service")
    def test_body_at_the_limit_passes(self, mock_svc_fn, client, monkeypatch):
        body = json.dumps({"name": "Boundary", "type": "tech"})
        monkeypatch.setattr("app.config.settings.max_request_body_bytes", len(body))
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
        resp = client.post("/api/v1/categories", headers=headers, content=body.encode())
        assert resp.status_code == 201

    @patch("app.api.v1.categories._get_category_service")
    def test_body_one_byte_over_the_limit_413(self, mock_svc_fn, client, monkeypatch):
        body = json.dumps({"name": "Boundary", "type": "tech"})
        monkeypatch.setattr("app.config.settings.max_request_body_bytes", len(body) - 1)
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}
        resp = client.post("/api/v1/categories", headers=headers, content=body.encode())
        _assert_body_envelope(resp.status_code, resp.json(), limit=len(body) - 1)
        mock_svc_fn.assert_not_called()

    @patch("app.api.v1.categories._get_category_service")
    def test_normal_sized_body_unaffected(self, mock_svc_fn, client):
        _mock_category_service(mock_svc_fn)
        headers = {"Authorization": f"Bearer {_token()}"}
        resp = client.post("/api/v1/categories", headers=headers, json={"name": "X", "type": "tech"})
        assert resp.status_code == 201
