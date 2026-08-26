"""Unit tests for the worker's runtime source scheduling hooks.

Covers the mapping between dashboard-channel source lifecycle events
(published by SourceService.update_source) and the scheduler manager calls,
plus the Redis Pub/Sub subscribe loop itself (mocked pubsub).
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.scheduler import worker as worker_mod


def _enabled_event(source_id=None, interval=120):
    sid = source_id or str(uuid.uuid4())
    return {
        "event": "source_enabled",
        "source_id": sid,
        "source": {
            "id": sid,
            "tenant_id": str(uuid.uuid4()),
            "category_id": str(uuid.uuid4()),
            "category_slug": "finance",
            "name": "RSS feed",
            "source_type": "rss",
            "url": "https://example.com/rss",
            "config": {},
            "refresh_interval_seconds": interval,
            "is_active": True,
            "priority": 5,
        },
    }


def _created_event(source_id=None, interval=120, is_active=True):
    event = _enabled_event(source_id, interval)
    event["event"] = "source_created"
    event["category_id"] = event["source"]["category_id"]
    event["name"] = event["source"]["name"]
    event["source"]["is_active"] = is_active
    return event


class TestHandleSourceStatusEvent:
    @pytest.fixture(autouse=True)
    def _stub_tenant_settings(self):
        # Keep unit tests DB-free: runtime enable/create events read tenant
        # settings through this helper (P2-16 refresh overrides).
        with patch.object(
            worker_mod, "_load_tenant_settings_for", new_callable=AsyncMock, return_value={}
        ) as mock_settings:
            yield mock_settings

    async def test_enabled_adds_job_from_payload(self):
        event = _enabled_event()
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.add_source_job = AsyncMock()
            mock_mgr.remove_source_job = AsyncMock()
            await worker_mod.handle_source_status_event(event)
            mock_mgr.add_source_job.assert_awaited_once_with(event["source"], {})
            mock_mgr.remove_source_job.assert_not_awaited()

    async def test_enabled_passes_tenant_settings_to_job(self):
        """Refresh overrides loaded for the source's tenant flow into add_source_job."""
        event = _enabled_event()
        settings = {"refresh_overrides": {"finance": 60}}
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(
                worker_mod, "_load_tenant_settings_for", new_callable=AsyncMock, return_value=settings
            ) as mock_settings,
        ):
            mock_mgr.add_source_job = AsyncMock()
            await worker_mod.handle_source_status_event(event)
            mock_settings.assert_awaited_once_with(event["source"]["tenant_id"])
            mock_mgr.add_source_job.assert_awaited_once_with(event["source"], settings)

    async def test_enabled_without_payload_falls_back_to_db(self):
        """Defensive path: if an enable event ever lacks the full source dict, the
        worker reloads it from the DB instead of scheduling a broken job."""
        sid = str(uuid.uuid4())
        db_payload = {"id": sid, "category_slug": "tech", "refresh_interval_seconds": 60, "source_type": "rss"}
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_source_payload", new_callable=AsyncMock) as mock_load,
        ):
            mock_mgr.add_source_job = AsyncMock()
            mock_load.return_value = db_payload
            await worker_mod.handle_source_status_event({"event": "source_enabled", "source_id": sid})
            mock_load.assert_awaited_once_with(sid)
            mock_mgr.add_source_job.assert_awaited_once_with(db_payload, {})

    async def test_enabled_db_fallback_missing_source_skips(self):
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_source_payload", new_callable=AsyncMock) as mock_load,
        ):
            mock_mgr.add_source_job = AsyncMock()
            mock_load.return_value = None
            await worker_mod.handle_source_status_event({"event": "source_enabled", "source_id": "ghost"})
            mock_mgr.add_source_job.assert_not_awaited()

    async def test_enabled_without_any_id_is_noop(self):
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_source_payload", new_callable=AsyncMock) as mock_load,
        ):
            mock_mgr.add_source_job = AsyncMock()
            await worker_mod.handle_source_status_event({"event": "source_enabled"})
            mock_mgr.add_source_job.assert_not_awaited()
            mock_load.assert_not_awaited()

    async def test_created_adds_job_from_payload(self):
        """A freshly created active source must start collecting immediately
        (same add_job path as source_enabled)."""
        assert "source_created" in worker_mod.SOURCE_EVENT_NAMES
        event = _created_event()
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.add_source_job = AsyncMock()
            mock_mgr.remove_source_job = AsyncMock()
            await worker_mod.handle_source_status_event(event)
            mock_mgr.add_source_job.assert_awaited_once_with(event["source"], {})
            mock_mgr.remove_source_job.assert_not_awaited()

    async def test_created_inactive_not_scheduled(self):
        event = _created_event(is_active=False)
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.add_source_job = AsyncMock()
            await worker_mod.handle_source_status_event(event)
            mock_mgr.add_source_job.assert_not_awaited()

    async def test_created_without_payload_falls_back_to_db(self):
        sid = str(uuid.uuid4())
        db_payload = {"id": sid, "category_slug": "tech", "refresh_interval_seconds": 60, "source_type": "rss"}
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_source_payload", new_callable=AsyncMock) as mock_load,
        ):
            mock_mgr.add_source_job = AsyncMock()
            mock_load.return_value = db_payload
            await worker_mod.handle_source_status_event({"event": "source_created", "source_id": sid})
            mock_load.assert_awaited_once_with(sid)
            mock_mgr.add_source_job.assert_awaited_once_with(db_payload, {})

    async def test_created_db_fallback_inactive_skips(self):
        """_load_source_payload returns None for inactive sources -> nothing scheduled."""
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_source_payload", new_callable=AsyncMock) as mock_load,
        ):
            mock_mgr.add_source_job = AsyncMock()
            mock_load.return_value = None
            await worker_mod.handle_source_status_event({"event": "source_created", "source_id": "new-src"})
            mock_mgr.add_source_job.assert_not_awaited()

    async def test_disabled_removes_job(self):
        sid = str(uuid.uuid4())
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.remove_source_job = AsyncMock()
            await worker_mod.handle_source_status_event({"event": "source_disabled", "source_id": sid})
            mock_mgr.remove_source_job.assert_awaited_once_with(sid)

    async def test_deleted_removes_job(self):
        sid = str(uuid.uuid4())
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.remove_source_job = AsyncMock()
            await worker_mod.handle_source_status_event({"event": "source_deleted", "source_id": sid})
            mock_mgr.remove_source_job.assert_awaited_once_with(sid)

    async def test_disabled_without_id_is_noop(self):
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.remove_source_job = AsyncMock()
            await worker_mod.handle_source_status_event({"event": "source_disabled"})
            mock_mgr.remove_source_job.assert_not_awaited()


def _mock_pubsub(messages):
    pubsub = MagicMock()
    pubsub.subscribe = AsyncMock()
    pubsub.unsubscribe = AsyncMock()
    pubsub.aclose = AsyncMock()

    async def _listen():
        for message in messages:
            yield message

    pubsub.listen = _listen
    return pubsub


class TestSourceEventListener:
    async def test_listener_dispatches_valid_events_only(self):
        enabled = _enabled_event()
        messages = [
            {"type": "subscribe", "data": None},
            {"type": "message", "data": "this is not json"},
            {"type": "message", "data": json.dumps({"event": "item_update", "data": {}})},
            {"type": "message", "data": json.dumps({"event": "source_disabled", "source_id": "src-off"})},
            {"type": "message", "data": json.dumps(enabled)},
        ]
        pubsub = _mock_pubsub(messages)
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub

        with (
            patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get,
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_tenant_settings_for", new_callable=AsyncMock, return_value={}),
        ):
            mock_get.return_value = redis_client
            mock_mgr.add_source_job = AsyncMock()
            mock_mgr.remove_source_job = AsyncMock()

            await worker_mod.source_event_listener()

            pubsub.subscribe.assert_awaited_once_with("channel:dashboard")
            mock_mgr.remove_source_job.assert_awaited_once_with("src-off")
            mock_mgr.add_source_job.assert_awaited_once_with(enabled["source"], {})
            pubsub.unsubscribe.assert_awaited_once_with("channel:dashboard")
            pubsub.aclose.assert_awaited_once()

    async def test_listener_dispatches_source_created(self):
        """source_created must not be filtered out by the pub/sub loop anymore."""
        created = _created_event()
        messages = [{"type": "message", "data": json.dumps(created)}]
        pubsub = _mock_pubsub(messages)
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub

        with (
            patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get,
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_tenant_settings_for", new_callable=AsyncMock, return_value={}),
        ):
            mock_get.return_value = redis_client
            mock_mgr.add_source_job = AsyncMock()
            await worker_mod.source_event_listener()
            mock_mgr.add_source_job.assert_awaited_once_with(created["source"], {})

    async def test_listener_sets_subscribed_after_subscribe(self):
        pubsub = _mock_pubsub([])
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub
        subscribed = asyncio.Event()

        with patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = redis_client
            await worker_mod.source_event_listener(subscribed)

        assert subscribed.is_set()

    async def test_listener_sets_subscribed_even_when_redis_fails(self):
        """Startup must not hang when Redis is unreachable: the subscribed event is
        set on the failure path too so main() continues with the DB snapshot."""
        subscribed = asyncio.Event()
        with patch.object(
            worker_mod, "get_redis_client", new_callable=AsyncMock, side_effect=ConnectionError("no redis")
        ):
            await worker_mod.source_event_listener(subscribed)
        assert subscribed.is_set()

    async def test_listener_cancellation_cleans_up_pubsub(self):
        never = asyncio.Event()

        async def _blocking_listen():
            await never.wait()
            yield {}  # pragma: no cover — never reached

        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.aclose = AsyncMock()
        pubsub.listen = _blocking_listen
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub

        with patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = redis_client
            task = asyncio.create_task(worker_mod.source_event_listener())
            await asyncio.sleep(0)
            pubsub.subscribe.assert_awaited_once()

            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        pubsub.unsubscribe.assert_awaited_once_with("channel:dashboard")
        pubsub.aclose.assert_awaited_once()


class TestSchedulerResumeEvent:
    """First-subscriber resume hook on the worker side (finance-tab.md §3.8.3):
    the api process publishes `scheduler_resume` on channel:dashboard when its
    SSE registry goes 0 → 1; the worker maps it to resume_paused_jobs()."""

    def test_event_name_is_accepted(self):
        assert worker_mod.SCHEDULER_RESUME_EVENT == "scheduler_resume"
        assert "scheduler_resume" in worker_mod.WORKER_EVENT_NAMES
        # Source lifecycle set stays intact (backward compat for other consumers).
        expected_source_events = {"source_created", "source_enabled", "source_disabled", "source_deleted"}
        assert set(worker_mod.SOURCE_EVENT_NAMES) == expected_source_events

    async def test_resume_event_calls_resume_paused_jobs(self):
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.resume_paused_jobs = AsyncMock(return_value=3)
            mock_mgr.add_source_job = AsyncMock()
            mock_mgr.remove_source_job = AsyncMock()
            await worker_mod.handle_source_status_event({"event": "scheduler_resume", "reason": "first_sse_subscriber"})
            mock_mgr.resume_paused_jobs.assert_awaited_once()
            mock_mgr.add_source_job.assert_not_awaited()
            mock_mgr.remove_source_job.assert_not_awaited()

    async def test_resume_event_no_paused_jobs_is_noop(self):
        with patch.object(worker_mod, "scheduler_manager") as mock_mgr:
            mock_mgr.resume_paused_jobs = AsyncMock(return_value=0)
            await worker_mod.handle_source_status_event({"event": "scheduler_resume"})
            mock_mgr.resume_paused_jobs.assert_awaited_once()

    async def test_listener_dispatches_scheduler_resume(self):
        messages = [
            {"type": "subscribe", "data": None},
            # SSE-client traffic on the same channel must be ignored.
            {"type": "message", "data": json.dumps({"event_type": "item_update", "data": {}})},
            {"type": "message", "data": json.dumps({"event": "scheduler_resume", "reason": "first_sse_subscriber"})},
        ]
        pubsub = _mock_pubsub(messages)
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub

        with (
            patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get,
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
        ):
            mock_get.return_value = redis_client
            mock_mgr.resume_paused_jobs = AsyncMock(return_value=0)

            await worker_mod.source_event_listener()

            mock_mgr.resume_paused_jobs.assert_awaited_once()
            pubsub.unsubscribe.assert_awaited_once_with("channel:dashboard")


class TestServiceToWorkerProtocol:
    """End-to-end protocol check at unit level: the exact payload SourceService
    publishes on enable/disable must be consumable by the worker handler."""

    async def test_update_source_payload_drives_worker_add_job(self):
        from app.schemas.source import SourceUpdate
        from app.services.source import SourceService

        src = MagicMock()
        src.id = uuid.uuid4()
        src.tenant_id = uuid.uuid4()
        src.category_id = uuid.uuid4()
        src.name = "RSS feed"
        src.source_type = "rss"
        src.url = "https://example.com/rss"
        src.config = {}
        src.refresh_interval_seconds = 120
        src.is_active = False
        src.priority = 5
        src.created_at = datetime.now(UTC)
        src.updated_at = datetime.now(UTC)
        src.health = None
        src.category = MagicMock()
        src.category.slug = "finance"
        src.category.refresh_interval_seconds = 300

        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar_one_or_none.return_value = src
            mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        with patch("app.services.source.redis_publish", new_callable=AsyncMock) as mock_publish:
            service = SourceService(db, AsyncMock())
            await service.update_source(str(src.id), SourceUpdate(is_active=True), str(src.tenant_id))

        mock_publish.assert_awaited_once()
        channel, message = mock_publish.call_args.args

        # Now feed the published message straight into the worker handler.
        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_tenant_settings_for", new_callable=AsyncMock, return_value={}),
        ):
            mock_mgr.add_source_job = AsyncMock()
            await worker_mod.handle_source_status_event(message)
            mock_mgr.add_source_job.assert_awaited_once_with(message["source"], {})

        assert channel == "channel:dashboard"
        # Fields the worker's add_source_job strictly needs:
        assert message["source"]["id"] == str(src.id)
        assert message["source"]["refresh_interval_seconds"] == 120
        assert message["source"]["source_type"] == "rss"
        assert message["source"]["category_slug"] == "finance"

    async def test_create_source_payload_drives_worker_add_job(self):
        """Regression: source_created used to carry only id/name, so the worker
        ignored it and freshly created active sources never started collecting.
        The published payload must now be consumable by handle_source_status_event."""
        from app.schemas.source import SourceCreate
        from app.services.source import SourceService

        tenant = MagicMock()
        tenant.id = "tenant-1"
        tenant.max_sources = 50

        category = MagicMock()
        category.id = uuid.uuid4()
        category.tenant_id = "tenant-1"
        category.slug = "finance"
        category.refresh_interval_seconds = 300

        db = AsyncMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()
        db.add = MagicMock()

        refetched = MagicMock()
        refetched.id = uuid.uuid4()
        refetched.category_id = category.id
        refetched.name = "Test RSS"
        refetched.source_type = "rss"
        refetched.url = "https://example.com/rss"
        refetched.config = {}
        refetched.refresh_interval_seconds = 300
        refetched.is_active = True
        refetched.priority = 5
        refetched.health = None
        refetched.category = category
        refetched.created_at = datetime.now(UTC)
        refetched.updated_at = datetime.now(UTC)

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = tenant
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = category
            else:
                mock_r.scalar_one.return_value = refetched
            return mock_r

        db.execute = execute_side_effect

        with (
            patch("app.services.source.redis_publish", new_callable=AsyncMock) as mock_publish,
            patch("app.services.source.redis_set", new_callable=AsyncMock),
        ):
            service = SourceService(db, AsyncMock())
            await service.create_source(
                SourceCreate(
                    name="Test RSS",
                    category_id=str(category.id),
                    source_type="rss",
                    url="https://example.com/rss",
                ),
                "tenant-1",
            )

        assert mock_publish.await_count == 1
        channel, message = mock_publish.call_args.args
        assert channel == "channel:dashboard"
        assert message["event"] == "source_created"

        with (
            patch.object(worker_mod, "scheduler_manager") as mock_mgr,
            patch.object(worker_mod, "_load_tenant_settings_for", new_callable=AsyncMock, return_value={}),
        ):
            mock_mgr.add_source_job = AsyncMock()
            await worker_mod.handle_source_status_event(message)
            mock_mgr.add_source_job.assert_awaited_once_with(message["source"], {})

        # Fields the worker's add_source_job strictly needs:
        assert message["source"]["refresh_interval_seconds"] == 300
        assert message["source"]["source_type"] == "rss"
        assert message["source"]["category_slug"] == "finance"
        assert message["source"]["is_active"] is True
