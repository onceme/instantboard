"""Tests for /api/v1/health endpoints."""

import uuid

from app.core.security import create_access_token


def _token(role="admin"):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": str(uuid.uuid4()),
            "role": role,
            "provider": "github",
            "type": "access",
        }
    )


def _headers(role="admin"):
    return {"Authorization": f"Bearer {_token(role=role)}"}


class TestHealthCheck:
    def test_health_returns_200(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_response_fields(self, client):
        data = client.get("/api/v1/health").json()
        assert data["status"] == "healthy"
        assert data["version"] == "1.0.0"
        assert "timestamp" in data

    def test_health_timestamp_is_iso_format(self, client):
        from datetime import datetime

        data = client.get("/api/v1/health").json()
        parsed = datetime.fromisoformat(data["timestamp"])
        assert parsed is not None


class TestHealthDetail:
    """/health/detail exposes PostgreSQL/Redis internals and the environment
    name, so it requires an admin token. /health stays public (CD smoke tests)."""

    def test_health_detail_no_token_401(self, client):
        resp = client.get("/api/v1/health/detail")
        assert resp.status_code == 401

    def test_health_detail_member_403(self, client):
        resp = client.get("/api/v1/health/detail", headers=_headers("member"))
        assert resp.status_code == 403

    def test_health_detail_viewer_403(self, client):
        resp = client.get("/api/v1/health/detail", headers=_headers("viewer"))
        assert resp.status_code == 403

    def test_health_detail_admin_200(self, client):
        resp = client.get("/api/v1/health/detail", headers=_headers("admin"))
        assert resp.status_code == 200

    def test_health_detail_has_postgres_status(self, client):
        data = client.get("/api/v1/health/detail", headers=_headers()).json()
        assert "details" in data
        assert "postgresql" in data["details"]
        pg = data["details"]["postgresql"]
        assert pg["status"] in ("healthy", "down")

    def test_health_detail_has_redis_status(self, client):
        data = client.get("/api/v1/health/detail", headers=_headers()).json()
        assert "details" in data
        assert "redis" in data["details"]
        redis = data["details"]["redis"]
        assert redis["status"] in ("healthy", "down")

    def test_health_detail_overall_status(self, client):
        data = client.get("/api/v1/health/detail", headers=_headers()).json()
        assert data["status"] in ("healthy", "degraded")

    def test_health_detail_version_and_environment(self, client):
        data = client.get("/api/v1/health/detail", headers=_headers()).json()
        assert data["version"] == "1.0.0"
        assert "environment" in data
        assert "timestamp" in data
