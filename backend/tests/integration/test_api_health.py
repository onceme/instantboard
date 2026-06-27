"""Tests for /api/v1/health endpoints."""


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
    def test_health_detail_returns_200(self, client):
        resp = client.get("/api/v1/health/detail")
        assert resp.status_code == 200

    def test_health_detail_has_postgres_status(self, client):
        data = client.get("/api/v1/health/detail").json()
        assert "details" in data
        assert "postgresql" in data["details"]
        pg = data["details"]["postgresql"]
        assert pg["status"] in ("healthy", "down")

    def test_health_detail_has_redis_status(self, client):
        data = client.get("/api/v1/health/detail").json()
        assert "details" in data
        assert "redis" in data["details"]
        redis = data["details"]["redis"]
        assert redis["status"] in ("healthy", "down")

    def test_health_detail_overall_status(self, client):
        data = client.get("/api/v1/health/detail").json()
        assert data["status"] in ("healthy", "degraded")

    def test_health_detail_version_and_environment(self, client):
        data = client.get("/api/v1/health/detail").json()
        assert data["version"] == "1.0.0"
        assert "environment" in data
        assert "timestamp" in data
