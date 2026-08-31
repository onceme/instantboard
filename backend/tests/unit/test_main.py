"""Unit tests for app/main.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI


class TestAppCreation:
    def test_app_title(self):
        from app.main import app

        assert app.title == "InstantBoard"

    def test_app_version(self):
        from app.main import app

        assert app.version == "1.0.0"

    def test_app_description(self):
        from app.main import app

        assert app.description == "Real-time information aggregation message board"

    def test_app_has_root_route(self):
        from app.main import app as _app

        route_paths = []
        for r in _app.routes:
            if hasattr(r, "path"):
                route_paths.append(r.path)
        assert "/" in route_paths


class TestRootEndpoint:
    async def test_root_with_no_start_time(self):
        import app.main as main_mod

        main_mod._start_time = None
        response = await main_mod.root()
        assert response.body is not None
        import json

        body = json.loads(response.body)
        assert body["name"] == "InstantBoard"
        assert body["version"] == "1.0.0"
        assert body["uptime_seconds"] == 0

    async def test_root_with_start_time(self):
        from datetime import UTC, datetime

        import app.main as main_mod

        main_mod._start_time = datetime.now(UTC)
        response = await main_mod.root()
        import json

        body = json.loads(response.body)
        assert body["name"] == "InstantBoard"
        assert body["uptime_seconds"] >= 0
        assert "timestamp" in body
        assert "api_docs" in body
        assert "environment" in body
        main_mod._start_time = None


class TestLifespan:
    async def test_lifespan_startup_shutdown(self):
        import app.main as main_mod
        from app.main import app as _app

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        # Create a real cancelled future that can be awaited
        mock_task = asyncio.Future()
        mock_task.cancel()

        with (
            patch("app.main.create_tables", new_callable=AsyncMock),
            patch("app.main.get_redis_client", new_callable=AsyncMock, return_value=mock_redis),
            patch("app.main.event_router") as mock_event_router,
        ):
            mock_event_router.start_redis_listener = AsyncMock(return_value=mock_task)
            mock_event_router.start_heartbeat = MagicMock(return_value=mock_task)
            mock_event_router.stop_heartbeat = MagicMock()
            mock_event_router.stop_redis_listener = MagicMock()
            with patch("app.main.settings") as mock_settings:
                mock_settings.sse_heartbeat_interval = 30
                mock_settings.scheduler_enabled = False
                mock_settings.log_level = "INFO"
                mock_settings.env = "development"
                with (
                    patch("asyncio.create_task", return_value=mock_task),
                    patch(
                        "app.services.dashboard.start_metrics_collection",
                        new_callable=AsyncMock,
                        return_value=mock_task,
                    ),
                    patch("app.services.dashboard.stop_metrics_collection"),
                    patch("app.main.close_redis", new_callable=AsyncMock),
                ):
                    async with main_mod.lifespan(_app):
                        pass

    async def test_lifespan_with_scheduler(self):
        import app.main as main_mod
        from app.main import app as _app

        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=False)

        # Create a real cancelled future that can be awaited
        mock_task = asyncio.Future()
        mock_task.cancel()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        with (
            patch("app.main.create_tables", new_callable=AsyncMock),
            patch("app.main.get_redis_client", new_callable=AsyncMock, return_value=mock_redis),
            patch("app.main.event_router") as mock_event_router,
        ):
            mock_event_router.start_redis_listener = AsyncMock(return_value=mock_task)
            mock_event_router.start_heartbeat = MagicMock(return_value=mock_task)
            mock_event_router.stop_heartbeat = MagicMock()
            mock_event_router.stop_redis_listener = MagicMock()
            with patch("app.main.settings") as mock_settings:
                mock_settings.sse_heartbeat_interval = 30
                mock_settings.scheduler_enabled = True
                mock_settings.log_level = "INFO"
                mock_settings.env = "development"
                with (
                    patch("asyncio.create_task", return_value=mock_task),
                    patch(
                        "app.services.dashboard.start_metrics_collection",
                        new_callable=AsyncMock,
                        return_value=mock_task,
                    ),
                    patch("app.services.dashboard.stop_metrics_collection"),
                    patch("app.main.close_redis", new_callable=AsyncMock),
                    patch("app.scheduler.manager.scheduler_manager") as mock_sched_mgr,
                ):
                    mock_sched_mgr.start = AsyncMock()
                    mock_sched_mgr.shutdown = AsyncMock()
                    mock_sched_mgr.schedule_all_active_sources = AsyncMock()
                    mock_sched_mgr.add_market_refresh_jobs = AsyncMock()
                    mock_sched_mgr.add_fund_nav_job = AsyncMock()
                    mock_sched_mgr.add_fund_intraday_jobs = AsyncMock()
                    mock_sched_mgr.add_fund_holdings_job = AsyncMock()
                    mock_sched_mgr.add_quote_partition_job = AsyncMock()
                    with patch("app.db.session.async_session_factory", return_value=mock_session):
                        async with main_mod.lifespan(_app):
                            mock_sched_mgr.start.assert_called_once()
                            # market indices/commodities refresh jobs ride the embedded
                            # scheduler (finance-tab.md §3.8.2)
                            mock_sched_mgr.add_market_refresh_jobs.assert_awaited_once()
                            # daily official fund NAV refresh (cron 20:00 Asia/Shanghai)
                            mock_sched_mgr.add_fund_nav_job.assert_awaited_once()
                            # fund intraday NAV estimate loop + daily holdings cron
                            # (fund-intraday-nav.md §7)
                            mock_sched_mgr.add_fund_intraday_jobs.assert_awaited_once()
                            mock_sched_mgr.add_fund_holdings_job.assert_awaited_once()
                            # daily finance_quotes partition roll (cron 00:30 UTC,
                            # database.md §3.1)
                            mock_sched_mgr.add_quote_partition_job.assert_awaited_once()
