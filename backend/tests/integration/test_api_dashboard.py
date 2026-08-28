"""Tests for /api/v1/dashboard endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.constants import SYSTEM_TENANT_ID
from app.core.security import create_access_token
from app.models.category import Category
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant
from tests.conftest import TEST_DATABASE_URL, test_session_factory

NOW = datetime.now(UTC).isoformat()


def _token(role="admin", tenant_id=None):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "role": role,
            "provider": "github",
            "type": "access",
        }
    )


def _admin_headers():
    return {"Authorization": f"Bearer {_token(role='admin')}"}


def _user_headers():
    return {"Authorization": f"Bearer {_token(role='user')}"}


class TestSystemInfo:
    @patch("app.api.v1.dashboard.DashboardService")
    def test_system_info_admin(self, mock_svc_cls, client):
        mock_svc = AsyncMock()
        mock_svc.get_system_info.return_value = {
            "version": "1.0.0",
            "uptime_seconds": 3600,
            "environment": "development",
            "python_version": "3.11.0",
            "cpu_count": 8,
            "cpu_usage_percent": 25.0,
            "memory_total_mb": 16384,
            "memory_used_mb": 8192,
            "disk_total_gb": 500.0,
            "disk_used_gb": 200.0,
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
            {
                "service": "postgresql",
                "status": "healthy",
                "response_time_ms": 5,
                "connection_count": 10,
                "details": None,
            },
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
            "total_sources": 10,
            "healthy": 8,
            "degraded": 1,
            "down": 1,
            "sources": [
                {
                    "id": str(uuid.uuid4()),
                    "name": "RSS",
                    "source_type": "rss",
                    "category_id": str(uuid.uuid4()),
                    "status": "healthy",
                    "success_rate_24h": 99.0,
                    "avg_response_time_ms": 100,
                    "last_success_at": NOW,
                    "last_failure_at": None,
                    "consecutive_failures": 0,
                    "total_fetches_24h": 200,
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
            "source_id": sid,
            "name": "RSS Feed",
            "source_type": "rss",
            "status": "healthy",
            "success_rate_24h": 99.5,
            "avg_response_time_ms": 120,
            "last_success_at": NOW,
            "last_failure_at": None,
            "consecutive_failures": 0,
            "total_fetches_24h": 100,
            "last_error": None,
            "health_history": [],
            "response_time_trend": [],
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
                    "job_id": "job-1",
                    "source_id": str(uuid.uuid4()),
                    "name": "Fetch RSS",
                    "schedule": "* * * * *",
                    "original_interval": 300,
                    "current_interval": 300,
                    "adaptive_multiplier": 1.0,
                    "last_run": NOW,
                    "next_run": NOW,
                    "status": "running",
                    "success_count_24h": 100,
                    "failure_count_24h": 0,
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

        with (
            patch("app.services.dashboard.DashboardService", return_value=mock_svc),
            patch("app.db.session.async_session_factory", return_value=mock_session),
            patch("app.core.redis.get_redis_client", new=AsyncMock(return_value=mock_redis)),
        ):
            resp = client.get("/api/v1/dashboard/scheduler", headers=_admin_headers())
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["total_jobs"] == 5

    def test_scheduler_non_admin(self, client):
        resp = client.get("/api/v1/dashboard/scheduler", headers=_user_headers())
        assert resp.status_code == 403


class TestSSEStats:
    @patch("app.db.session.async_session_factory")
    # Post-RLS the endpoint imports DashboardService inside the function (it
    # opens its own service-context session), so patch the source module.
    @patch("app.services.dashboard.DashboardService")
    def test_sse_stats(self, mock_svc_cls, mock_factory, client):
        session = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = ctx
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


@pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason=(
        "End-to-end path binds the JWT tenant id (always a str) against UUID columns; "
        "native uuid handling for str values requires PostgreSQL (SQLite's Uuid bind "
        "processor needs uuid.UUID instances). Same rationale as test_pg_tech_system_tenant."
    ),
)
class TestDataSourceHealthDetailTenantScopeE2E:
    """End-to-end: the detail endpoint must enforce the same tenant rule as the list
    endpoint (own tenant + system tenant; anything else 404). Runs the real service
    against the test DB using a fresh tenant per run."""

    @pytest_asyncio.fixture
    async def aclient(self, app_with_overrides):
        app, _ = app_with_overrides
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    async def test_own_and_system_visible_foreign_404(self, aclient):
        suffix = uuid.uuid4().hex[:8]
        async with test_session_factory() as session:
            system_tenant = await session.get(Tenant, SYSTEM_TENANT_ID)
            if system_tenant is None:
                system_tenant = Tenant(name="System", slug=f"sys-{suffix}")
                system_tenant.id = SYSTEM_TENANT_ID
                session.add(system_tenant)
                await session.flush()

            own_tenant = Tenant(name="Scope Own", slug=f"scope-own-{suffix}", plan="free", settings={})
            foreign_tenant = Tenant(name="Scope Foreign", slug=f"scope-for-{suffix}", plan="free", settings={})
            session.add_all([own_tenant, foreign_tenant])
            await session.flush()

            cat_own = Category(tenant_id=own_tenant.id, name="Own Cat", slug=f"own-{suffix}", type="news")
            cat_sys = Category(tenant_id=system_tenant.id, name="Sys Cat", slug=f"sys-{suffix}", type="news")
            cat_for = Category(tenant_id=foreign_tenant.id, name="For Cat", slug=f"for-{suffix}", type="news")
            session.add_all([cat_own, cat_sys, cat_for])
            await session.flush()

            source_own = Source(
                tenant_id=own_tenant.id,
                category_id=cat_own.id,
                name="Own Source",
                source_type="rss",
                url="https://own.example.com/rss",
                is_active=True,
            )
            source_sys = Source(
                tenant_id=system_tenant.id,
                category_id=cat_sys.id,
                name="System Source",
                source_type="rss",
                url="https://system.example.com/rss",
                is_active=True,
            )
            source_foreign = Source(
                tenant_id=foreign_tenant.id,
                category_id=cat_for.id,
                name="Foreign Source",
                source_type="rss",
                url="https://foreign.example.com/rss",
                is_active=True,
            )
            session.add_all([source_own, source_sys, source_foreign])
            await session.flush()

            session.add_all(
                [
                    SourceHealth(source_id=source_own.id, status="healthy"),
                    SourceHealth(source_id=source_sys.id, status="healthy"),
                    SourceHealth(source_id=source_foreign.id, status="healthy"),
                ]
            )
            await session.commit()

            own_id = own_tenant.id
            own_src, sys_src, foreign_src = source_own.id, source_sys.id, source_foreign.id

        headers = {"Authorization": f"Bearer {_token(role='admin', tenant_id=str(own_id))}"}

        resp = await aclient.get(f"/api/v1/dashboard/data-sources/{own_src}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "Own Source"

        resp = await aclient.get(f"/api/v1/dashboard/data-sources/{sys_src}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["name"] == "System Source"

        resp = await aclient.get(f"/api/v1/dashboard/data-sources/{foreign_src}", headers=headers)
        assert resp.status_code == 404
        assert resp.json()["detail"]["error"]["code"] == "SOURCE_NOT_FOUND"
