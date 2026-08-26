"""Unit tests for tenant-aware effective interval resolution (P2-16).

Covers resolve_effective_interval's three-branch priority (tenant override →
source interval → source_type default), its tolerance of malformed override
data, and the wiring of the resolution into the scheduler manager's
registration paths (schedule_all_active_sources rebuild + add_source_job
runtime events).
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.scheduler.manager import (
    SOURCE_TYPE_DEFAULT_INTERVALS,
    AsyncSchedulerManager,
    resolve_effective_interval,
)


def _orm_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", str(uuid.uuid4()))
    src.tenant_id = kwargs.get("tenant_id", "tenant-1")
    src.is_active = kwargs.get("is_active", True)
    src.refresh_interval_seconds = kwargs.get("refresh_interval_seconds", 300)
    src.source_type = kwargs.get("source_type", "rss")
    category = MagicMock()
    category.slug = kwargs.get("category_slug", "finance")
    src.category = kwargs.get("category", category)
    return src


class TestResolveEffectiveInterval:
    def test_override_wins_over_source_interval(self):
        settings = {"refresh_overrides": {"finance": 60}}
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, "finance", settings) == 60

    def test_override_wins_for_dict_payload_too(self):
        settings = {"refresh_overrides": {"tech": 45}}
        payload = {"refresh_interval_seconds": 120, "source_type": "rss"}
        assert resolve_effective_interval(payload, "tech", settings) == 45

    def test_no_override_falls_back_to_source_interval(self):
        settings = {"refresh_overrides": {"finance": 60}}
        src = _orm_source(refresh_interval_seconds=600)
        assert resolve_effective_interval(src, "tech", settings) == 600

    def test_no_settings_falls_back_to_source_interval(self):
        src = _orm_source(refresh_interval_seconds=600)
        assert resolve_effective_interval(src, "finance", None) == 600
        assert resolve_effective_interval(src, "finance", {}) == 600

    def test_no_source_interval_falls_back_to_type_default(self):
        src = _orm_source(refresh_interval_seconds=None, source_type="rss")
        assert resolve_effective_interval(src, "finance", None) == SOURCE_TYPE_DEFAULT_INTERVALS["rss"]

    def test_unknown_source_type_defaults_to_300(self):
        src = _orm_source(refresh_interval_seconds=None, source_type="carrier-pigeon")
        assert resolve_effective_interval(src, None, None) == 300

    def test_too_low_source_interval_falls_back_to_type_default(self):
        src = _orm_source(refresh_interval_seconds=5, source_type="api")
        assert resolve_effective_interval(src, "finance", None) == SOURCE_TYPE_DEFAULT_INTERVALS["api"]

    def test_malformed_override_type_is_ignored(self):
        settings = {"refresh_overrides": {"finance": "sixty"}}
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, "finance", settings) == 300

    def test_bool_override_is_ignored(self):
        settings = {"refresh_overrides": {"finance": True}}
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, "finance", settings) == 300

    def test_out_of_range_override_is_ignored(self):
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, "finance", {"refresh_overrides": {"finance": 9}}) == 300
        assert resolve_effective_interval(src, "finance", {"refresh_overrides": {"finance": 86401}}) == 300

    def test_non_dict_overrides_container_is_ignored(self):
        settings = {"refresh_overrides": [60]}
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, "finance", settings) == 300

    def test_no_category_slug_skips_override(self):
        settings = {"refresh_overrides": {"finance": 60}}
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, None, settings) == 300
        assert resolve_effective_interval(src, "", settings) == 300

    def test_override_bounds_are_inclusive(self):
        src = _orm_source(refresh_interval_seconds=300)
        assert resolve_effective_interval(src, "finance", {"refresh_overrides": {"finance": 10}}) == 10
        assert resolve_effective_interval(src, "finance", {"refresh_overrides": {"finance": 86400}}) == 86400


def _make_mock_scheduler():
    mock_sched = MagicMock()
    mock_sched.get_job = MagicMock(return_value=None)
    mock_sched.add_job = MagicMock()
    return mock_sched


class TestScheduleAllActiveSourcesWithOverrides:
    async def test_rebuild_applies_tenant_overrides_from_map(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.add_job = AsyncMock()

            src = _orm_source(tenant_id="tenant-1", category_slug="finance", refresh_interval_seconds=300)
            settings_map = {"tenant-1": {"refresh_overrides": {"finance": 90}}}

            await mgr.schedule_all_active_sources([src], settings_map)

            mgr.add_job.assert_awaited_once()
            assert mgr.add_job.await_args.kwargs["interval_seconds"] == 90

    async def test_rebuild_without_map_keeps_source_interval(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.add_job = AsyncMock()

            src = _orm_source(tenant_id="tenant-1", category_slug="finance", refresh_interval_seconds=300)
            await mgr.schedule_all_active_sources([src])

            assert mgr.add_job.await_args.kwargs["interval_seconds"] == 300

    async def test_rebuild_map_miss_for_other_tenant_keeps_source_interval(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.add_job = AsyncMock()

            src = _orm_source(tenant_id="tenant-1", category_slug="finance", refresh_interval_seconds=300)
            settings_map = {"tenant-2": {"refresh_overrides": {"finance": 90}}}

            await mgr.schedule_all_active_sources([src], settings_map)

            assert mgr.add_job.await_args.kwargs["interval_seconds"] == 300

    async def test_rebuild_inactive_sources_are_skipped(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.add_job = AsyncMock()

            src = _orm_source(is_active=False)
            await mgr.schedule_all_active_sources([src], {})

            mgr.add_job.assert_not_awaited()


class TestAddSourceJobWithOverrides:
    async def test_event_path_applies_tenant_overrides(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.add_collection_job = AsyncMock()

            payload = {
                "id": "src-1",
                "category_slug": "finance",
                "refresh_interval_seconds": 300,
                "source_type": "rss",
            }
            settings = {"refresh_overrides": {"finance": 75}}

            await mgr.add_source_job(payload, settings)

            mgr.add_collection_job.assert_awaited_once()
            assert mgr.add_collection_job.await_args.kwargs["interval_seconds"] == 75

    async def test_event_path_without_settings_uses_source_interval(self):
        with patch("app.scheduler.manager.AsyncIOScheduler") as mock_cls:
            mock_cls.return_value = _make_mock_scheduler()
            mgr = AsyncSchedulerManager()
            mgr.add_collection_job = AsyncMock()

            payload = {
                "id": "src-1",
                "category_slug": "finance",
                "refresh_interval_seconds": 120,
                "source_type": "rss",
            }

            await mgr.add_source_job(payload)

            assert mgr.add_collection_job.await_args.kwargs["interval_seconds"] == 120
