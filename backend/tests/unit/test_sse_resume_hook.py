"""Unit tests for the SSE load gauge and the first-subscriber resume hook
(finance-tab.md §3.8.3).

Covers the api-process side of the cross-process signal:
  - _sync_active_connections_gauge(): writes the connection count with TTL,
    tolerates Redis failures;
  - _signal_first_subscriber_resume(): embedded-scheduler direct resume (dev)
    + channel:dashboard scheduler_resume publish (prod), error tolerance;
  - register()/unregister() on the process-wide router: the 0 → 1 edge
    triggers the resume signal exactly once, reconnects don't repeat it, and
    the gauge follows +1/-1; local instances stay side-effect free.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys
from app.core.sse_router import (
    SSEEventRouter,
    _signal_first_subscriber_resume,
    _spawn_background_hook,
    _sync_active_connections_gauge,
    event_router,
)

TENANT = str(SYSTEM_TENANT_ID)


class TestSyncActiveConnectionsGauge:
    async def test_writes_count_with_ttl(self):
        event_router._connections = {}
        event_router._subscriptions = {}
        client = MagicMock()
        client.set = AsyncMock()

        event_router.register("gauge-c1", ["finance"], TENANT)
        try:
            with patch("app.core.sse_router.get_redis_client", new_callable=AsyncMock, return_value=client):
                await _sync_active_connections_gauge()
        finally:
            event_router.unregister("gauge-c1")
            event_router._connections = {}
            event_router._subscriptions = {}

        client.set.assert_awaited_once_with(
            RedisKeys.sse_active_connections_key(), 1, ex=RedisKeys.SSE_ACTIVE_CONNECTIONS_TTL
        )

    async def test_writes_zero_after_last_disconnect(self):
        event_router._connections = {}
        event_router._subscriptions = {}
        client = MagicMock()
        client.set = AsyncMock()

        with patch("app.core.sse_router.get_redis_client", new_callable=AsyncMock, return_value=client):
            await _sync_active_connections_gauge()

        client.set.assert_awaited_once_with(
            RedisKeys.sse_active_connections_key(), 0, ex=RedisKeys.SSE_ACTIVE_CONNECTIONS_TTL
        )

    async def test_redis_failure_is_swallowed(self):
        with patch(
            "app.core.sse_router.get_redis_client", new_callable=AsyncMock, side_effect=ConnectionError("no redis")
        ):
            # Must not raise: the connect/disconnect path never feels Redis trouble.
            await _sync_active_connections_gauge()


class TestSignalFirstSubscriberResume:
    async def test_dev_embedded_scheduler_resumes_directly_and_publishes(self):
        from app.scheduler.manager import scheduler_manager as real_manager

        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = True

        with (
            patch("app.config.settings", mock_settings),
            patch.object(real_manager, "resume_paused_jobs", new_callable=AsyncMock, return_value=2) as mock_resume,
            patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish,
        ):
            await _signal_first_subscriber_resume()

        mock_resume.assert_awaited_once()
        # The publish always fires as well (prod delivery path; harmless in dev).
        mock_publish.assert_awaited_once()
        channel, message = mock_publish.await_args.args
        assert channel == RedisKeys.channel_key("dashboard")
        assert message["event"] == "scheduler_resume"
        assert message["reason"] == "first_sse_subscriber"

    async def test_prod_publishes_without_direct_resume(self):
        from app.scheduler.manager import scheduler_manager as real_manager

        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = False

        with (
            patch("app.config.settings", mock_settings),
            patch.object(real_manager, "resume_paused_jobs", new_callable=AsyncMock) as mock_resume,
            patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish,
        ):
            await _signal_first_subscriber_resume()

        mock_resume.assert_not_awaited()
        mock_publish.assert_awaited_once()

    async def test_resume_error_is_swallowed_and_publish_still_fires(self):
        from app.scheduler.manager import scheduler_manager as real_manager

        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = True

        with (
            patch("app.config.settings", mock_settings),
            patch.object(real_manager, "resume_paused_jobs", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
            patch("app.core.sse_router.redis_publish", new_callable=AsyncMock) as mock_publish,
        ):
            await _signal_first_subscriber_resume()

        mock_publish.assert_awaited_once()

    async def test_publish_error_is_swallowed(self):
        mock_settings = MagicMock()
        mock_settings.scheduler_enabled = False

        with (
            patch("app.config.settings", mock_settings),
            patch("app.core.sse_router.redis_publish", new_callable=AsyncMock, side_effect=ConnectionError("gone")),
        ):
            await _signal_first_subscriber_resume()  # must not raise


class TestRegisterTriggersResumeHook:
    """0 → 1 registry edge on the process-wide router fires the resume signal
    exactly once; later registrations only refresh the gauge."""

    def setup_method(self):
        event_router._connections = {}
        event_router._subscriptions = {}

    def teardown_method(self):
        event_router._connections = {}
        event_router._subscriptions = {}

    async def _settle(self):
        # Let the fire-and-forget tasks spawned by register()/unregister() run.
        for _ in range(3):
            await asyncio.sleep(0)

    async def test_first_subscriber_fires_resume_signal_once(self):
        with (
            patch("app.core.sse_router._sync_active_connections_gauge", new_callable=AsyncMock) as mock_gauge,
            patch("app.core.sse_router._signal_first_subscriber_resume", new_callable=AsyncMock) as mock_signal,
        ):
            event_router.register("hook-c1", ["finance"], TENANT)
            await self._settle()
            mock_signal.assert_awaited_once()
            mock_gauge.assert_awaited_once()

            # Second connection: gauge refreshed, resume signal NOT repeated.
            event_router.register("hook-c2", ["tech"], TENANT)
            await self._settle()
            mock_signal.assert_awaited_once()
            assert mock_gauge.await_count == 2

    async def test_signal_refires_after_registry_went_empty(self):
        with (
            patch("app.core.sse_router._sync_active_connections_gauge", new_callable=AsyncMock),
            patch("app.core.sse_router._signal_first_subscriber_resume", new_callable=AsyncMock) as mock_signal,
        ):
            event_router.register("hook-c1", ["finance"], TENANT)
            await self._settle()
            event_router.unregister("hook-c1")
            await self._settle()

            # Registry is empty again → the next connection is a 0 → 1 edge.
            event_router.register("hook-c2", ["finance"], TENANT)
            await self._settle()
            assert mock_signal.await_count == 2

    async def test_unregister_refreshes_gauge_only(self):
        with (
            patch("app.core.sse_router._sync_active_connections_gauge", new_callable=AsyncMock) as mock_gauge,
            patch("app.core.sse_router._signal_first_subscriber_resume", new_callable=AsyncMock) as mock_signal,
        ):
            event_router.register("hook-c1", ["finance"], TENANT)
            await self._settle()
            mock_gauge.assert_awaited_once()

            event_router.unregister("hook-c1")
            await self._settle()
            assert mock_gauge.await_count == 2
            mock_signal.assert_awaited_once()  # no resume on disconnect

    async def test_gauge_follows_plus_one_minus_one(self):
        # Real gauge function, mocked redis client: the key value tracks the
        # registry 0 → 1 → 0 across connect/disconnect.
        client = MagicMock()
        client.set = AsyncMock()
        client.publish = AsyncMock()

        with patch("app.core.sse_router.get_redis_client", new_callable=AsyncMock, return_value=client):
            event_router.register("hook-c1", ["finance"], TENANT)
            await self._settle()
            assert client.set.await_args.args == (RedisKeys.sse_active_connections_key(), 1)

            event_router.unregister("hook-c1")
            await self._settle()
            assert client.set.await_args.args == (RedisKeys.sse_active_connections_key(), 0)

    async def test_local_instance_has_no_side_effects(self):
        # Only the process-wide singleton drives the cross-process hooks; a
        # local instance (unit-test scenario) must not publish or gauge-sync.
        local = SSEEventRouter()
        with (
            patch("app.core.sse_router._sync_active_connections_gauge", new_callable=AsyncMock) as mock_gauge,
            patch("app.core.sse_router._signal_first_subscriber_resume", new_callable=AsyncMock) as mock_signal,
        ):
            local.register("local-c1", ["finance"], TENANT)
            local.unregister("local-c1")
            await self._settle()
            mock_signal.assert_not_awaited()
            mock_gauge.assert_not_awaited()


class TestSpawnBackgroundHook:
    def test_sync_context_drops_coroutine_silently(self):
        # No running event loop (sync call path): the coroutine is closed and
        # nothing raises.
        coro = AsyncMock(return_value=None)()
        _spawn_background_hook(coro)
        # The coroutine object was closed rather than leaked.
        assert coro.cr_frame is None
