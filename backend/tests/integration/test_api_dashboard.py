"""Tests for /api/v1/dashboard endpoints."""
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


def _admin_headers():
    return {"Authorization": f"Bearer {_token(role='admin')}"}


def _user_headers():
    return {"Authorization": f"Bearer {_token(role='user')}"}


class TestSystemInfo:
    @patch("app.api.v1.dashboard.DashboardService")
    def test_system_info_admin(self, mock_svc_cls, client):
        mock_svc = AsyncMock()
        mock_svc.get_system_info.return_value = {
            "version": "1.0.0", "uptime_seconds": 3600, "environment": "development",
            "python_version": "3.11.0", "cpu_count": 8, "cpu_usage_percent": 25.0,
            "memory_total_mb": 16384, "memory_used_mb": 8192,
            "disk_total_gb": 500.0, "disk_used_gb": 200.0,
        }
        mock_svc_cls.return_value = mock_svc

        resp = client.get("/api/v1/dashboard/system", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["version"] == "1.0.0"

    def test_system_info_non_admin_forbidden(self, client):
        resp = client.get("/api/v1/dashboard/system", headers=_user_headers())
        assert resp.status_code == 403

    def test_system_info_no_auth(self, client):
        resp = client.get("/api/v1/dashboard/system")
        assert resp.status_code == 401


class TestServicesHealth:
    @patch("app.api.v1.dashboard.DashboardService")
    def test_services_health(self, mock_svc_cls, client):
        mock_svc = AsyncMock()
        mock_svc.get_services_health.return_value = [
            {"service": "postgresql", "status": "healthy", "response_time_ms": 5, "connection_count": 10, "details": None},
            {"service": "redis", "status": "healthy", "response_time_ms": 2, "connection_count": 5, "details": None},
        ]
        mock_svc_cls.return_value = mock_svc

        resp = client.get("/api/v1/dashboard/services", headers=_admin_headers())
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_services_health_non_admin(self, client):
        resp = client.get("/api/v1/dashboard/services", headers=_user_headers())
        assert resp.status_code == 403


class TestDataSourcesHealth:
    @patch("app.api.v1.dashboard.DashboardService")
    def test_data_sources_health(self, mock_svc_cls, client):
        mock_svc = AsyncMock()
        mock_svc.get_data_sources_health_summary.return_value = {
            "total_sources": 10, "healthy": 8, "degraded": 1, "down": 1,
            "sources": [
                {
                    "id": str(uuid.uuid4()), "name": "RSS", "source_type": "rss",
                    "category_id": str(uuid.uuid4()), "status": "healthy",
                    "success_rate_24h": 99.0, "avg_response_time_ms": 100,
                    "last_success_at": NOW, "last_failure_at": None,
                    "consecutive_failures": 0, "total_fetches_24h": 200,
                    "last_error": None,
                },
            ],
        }
        mock_svc_cls.return_value = mock_svc

        resp = client.get("/api/v1/dashboard/data-sources", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["total_sources"] == 10

    def test_data_sources_non_admin(self, client):
        resp = client.get("/api/v1/dashboard/data-sources", headers=_user_headers())
        assert resp.status_code == 403


class TestDataSourceHealthDetail:
    @patch("app.api.v1.dashboard.DashboardService")
    def test_detail_found(self, mock_svc_cls, client):
        sid = str(uuid.uuid4())
        mock_svc = AsyncMock()
        mock_svc.get_data_source_health_detail.return_value = {
            "source_id": sid, "name": "RSS Feed", "source_type": "rss",
            "status": "healthy", "success_rate_24h": 99.5,
            "avg_response_time_ms": 120, "last_success_at": NOW,
            "last_failure_at": None, "consecutive_failures": 0,
            "total_fetches_24h": 100, "last_error": None,
            "health_history": [], "response_time_trend": [],
        }
        mock_svc_cls.return_value = mock_svc

        resp = client.get(f"/api/v1/dashboard/data-sources/{sid}", headers=_admin_headers())
        assert resp.status_code == 200

    @patch("app.api.v1.dashboard.DashboardService")
    def test_detail_not_found(self, mock_svc_cls, client):
        mock_svc = AsyncMock()
        mock_svc.get_data_source_health_detail.return_value = None
        mock_svc_cls.return_value = mock_svc

        resp = client.get(f"/api/v1/dashboard/data-sources/{uuid.uuid4()}", headers=_admin_headers())
        assert resp.status_code == 404


class TestSchedulerStatus:
    def test_scheduler_status(self, client):
        mock_svc = AsyncMock()
        mock_svc.get_scheduler_status.return_value = {
            "total_jobs": 5,
            "running_jobs": [
                {
                    "job_id": "job-1", "source_id": str(uuid.uuid4()),
                    "name": "Fetch RSS", "schedule": "* * * * *",
                    "original_interval": 300, "current_interval": 300,
                    "adaptive_multiplier": 1.0, "last_run": NOW, "next_run": NOW,
                    "status": "running", "success_count_24h": 100, "failure_count_24h": 0,
                },
            ],
            "paused_jobs": [],
            "all_jobs": [],
        }

        # The scheduler endpoint uses local imports inside the function body:
        #   from app.core.redis import get_redis_client
        #   from app.db.session import async_session_factory
        #   from app.services.dashboard import DashboardService
        # So we must patch them at their source modules, not at app.api.v1.dashboard.
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_redis = AsyncMock()

        with patch("app.services.dashboard.DashboardService", return_value=mock_svc), \
             patch("app.db.session.async_session_factory", return_value=mock_session), \
             patch("app.core.redis.get_redis_client", new=AsyncMock(return_value=mock_redis)):
            resp = client.get("/api/v1/dashboard/scheduler", headers=_admin_headers())
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_jobs"] == 5

    def test_scheduler_non_admin(self, client):
        resp = client.get("/api/v1/dashboard/scheduler", headers=_user_headers())
        assert resp.status_code == 403


class TestSSEStats:
    @patch("app.api.v1.dashboard.DashboardService")
    def test_sse_stats(self, mock_svc_cls, client):
        mock_svc = AsyncMock()
        mock_svc.get_sse_stats.return_value = {
            "total_connections": 15,
            "connections_by_channel": {"finance": 10, "tech": 5},
            "peak_connections_24h": 25,
            "peak_connections_today": 20,
            "total_connections_today": 42,
            "total_events_pushed": 1000,
            "average_events_per_minute": 3.5,
            "avg_connection_duration_seconds": 360.0,
        }
        mock_svc_cls.return_value = mock_svc

        resp = client.get("/api/v1/dashboard/sse-stats", headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.json()["data"]["total_connections"] == 15
