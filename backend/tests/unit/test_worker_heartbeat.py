"""Unit tests for the worker heartbeat mechanism.

The worker publishes a JSON heartbeat to Redis (scheduler:worker:heartbeat) so
the api process — whose embedded scheduler is disabled in prod — can report
worker health instead of always showing the scheduler as down. Covers the
payload contract, the SET with EX=TTL, error swallowing (a heartbeat failure
must never kill the worker) and the best-effort key cleanup on shutdown.
Contract: docs/dev-guide/design/infrastructure.md §3.5 "Worker 心跳机制".
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.redis import RedisKeys
from app.scheduler import worker as worker_mod

HEARTBEAT_KEY = RedisKeys.worker_heartbeat_key()
EXPECTED_FIELDS = {
    "timestamp",
    "pid",
    "scheduler_running",
    "jobs_total",
    "jobs_running",
    "jobs_paused",
    "event_listener_subscribed",
}


def _mock_scheduler(jobs=None, running=True):
    mgr = MagicMock()
    mgr._running = running
    mgr.get_jobs_status = AsyncMock(return_value=jobs if jobs is not None else [])
    return mgr


class TestBuildHeartbeatPayload:
    async def test_payload_contract_seven_fields(self):
        """The payload must carry exactly the fields the api side reads."""
        jobs = [
            {"job_id": "collect_a", "pending": False},
            {"job_id": "collect_b", "pending": False},
            {"job_id": "collect_c", "pending": True},
        ]
        with patch.object(worker_mod, "scheduler_manager", _mock_scheduler(jobs)):
            payload = await worker_mod.build_heartbeat_payload({worker_mod.LISTENER_STATE_SUBSCRIBED: True})

        assert set(payload.keys()) == EXPECTED_FIELDS
        assert payload["scheduler_running"] is True
        assert payload["jobs_total"] == 3
        assert payload["jobs_running"] == 2
        assert payload["jobs_paused"] == 1
        assert payload["event_listener_subscribed"] is True
        assert payload["pid"] > 0
        # ISO-8601 timestamp (parseable on the api side)
        from datetime import UTC, datetime

        parsed = datetime.fromisoformat(payload["timestamp"])
        assert parsed.tzinfo is not None
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 5

    async def test_listener_not_subscribed_reflected(self):
        """A dead/never-subscribed event listener must show up in the heartbeat."""
        with patch.object(worker_mod, "scheduler_manager", _mock_scheduler()):
            payload = await worker_mod.build_heartbeat_payload({worker_mod.LISTENER_STATE_SUBSCRIBED: False})
        assert payload["event_listener_subscribed"] is False

        # No state dict at all (defensive) also means not subscribed.
        with patch.object(worker_mod, "scheduler_manager", _mock_scheduler()):
            payload = await worker_mod.build_heartbeat_payload(None)
        assert payload["event_listener_subscribed"] is False

    async def test_scheduler_stopped_flag(self):
        with patch.object(worker_mod, "scheduler_manager", _mock_scheduler(running=False)):
            payload = await worker_mod.build_heartbeat_payload(None)
        assert payload["scheduler_running"] is False


class TestWriteHeartbeat:
    async def test_writes_key_with_ttl_and_payload(self):
        """SET scheduler:worker:heartbeat <json> EX=45 — the whole contract."""
        client = AsyncMock()
        jobs = [{"job_id": "collect_a", "pending": False}]
        with (
            patch.object(worker_mod, "scheduler_manager", _mock_scheduler(jobs)),
            patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.return_value = client
            await worker_mod.write_heartbeat({worker_mod.LISTENER_STATE_SUBSCRIBED: True})

        client.set.assert_awaited_once()
        key, value = client.set.call_args.args
        assert key == HEARTBEAT_KEY
        assert client.set.call_args.kwargs.get("ex") == RedisKeys.WORKER_HEARTBEAT_TTL
        assert RedisKeys.WORKER_HEARTBEAT_TTL == 3 * worker_mod.HEARTBEAT_INTERVAL_SECONDS

        payload = json.loads(value)
        assert set(payload.keys()) == EXPECTED_FIELDS
        assert payload["jobs_total"] == 1
        assert payload["jobs_running"] == 1
        assert payload["jobs_paused"] == 0
        assert payload["event_listener_subscribed"] is True

    async def test_redis_error_does_not_raise(self):
        """A heartbeat failure is logged, never propagated — it must not kill the worker."""
        with (
            patch.object(worker_mod, "scheduler_manager", _mock_scheduler()),
            patch.object(
                worker_mod, "get_redis_client", new_callable=AsyncMock, side_effect=ConnectionError("no redis")
            ),
        ):
            await worker_mod.write_heartbeat(None)  # must not raise

    async def test_jobs_status_error_does_not_raise(self):
        """Scheduler status errors are swallowed too."""
        mgr = _mock_scheduler()
        mgr.get_jobs_status = AsyncMock(side_effect=RuntimeError("scheduler exploded"))
        with (
            patch.object(worker_mod, "scheduler_manager", mgr),
            patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock),
        ):
            await worker_mod.write_heartbeat(None)  # must not raise


class TestHeartbeatLoop:
    async def test_loop_writes_then_sleeps_one_interval(self):
        real_sleep = asyncio.sleep
        iterations = 0
        sleep_seconds = []

        async def fake_sleep(seconds):
            nonlocal iterations
            sleep_seconds.append(seconds)
            iterations += 1
            await real_sleep(0)  # yield control without the 15s wall-clock wait

        write_calls = []

        async def fake_write(state=None):
            write_calls.append(state)

        with (
            patch.object(worker_mod, "write_heartbeat", new=fake_write),
            patch.object(worker_mod.asyncio, "sleep", new=fake_sleep),
        ):
            state = {worker_mod.LISTENER_STATE_SUBSCRIBED: True}
            task = asyncio.create_task(worker_mod.heartbeat_loop(state))
            for _ in range(10):
                await real_sleep(0)  # let a few loop cycles run
            task.cancel()

        assert len(write_calls) >= 2, "the loop must keep refreshing the heartbeat"
        assert all(s is state for s in write_calls)
        # Every sleep must use the configured interval (15s)
        assert iterations >= 1
        assert all(s == worker_mod.HEARTBEAT_INTERVAL_SECONDS for s in sleep_seconds)

    async def test_loop_survives_write_errors(self):
        """If write_heartbeat itself ever raised (should not, but defensively),
        the loop must keep running — a transient failure must not stop heartbeating."""
        real_sleep = asyncio.sleep

        async def fake_sleep(seconds):
            await real_sleep(0)

        calls = 0

        async def flaky_write(state=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("unexpected failure")

        with (
            patch.object(worker_mod, "write_heartbeat", new=flaky_write),
            patch.object(worker_mod.asyncio, "sleep", new=fake_sleep),
        ):
            task = asyncio.create_task(worker_mod.heartbeat_loop({}))
            try:
                for _ in range(10):
                    await real_sleep(0)
                assert calls >= 3, "loop must survive an exception and keep heartbeating"
            finally:
                task.cancel()


class TestClearHeartbeat:
    async def test_deletes_key(self):
        client = AsyncMock()
        with patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = client
            await worker_mod.clear_heartbeat()
        client.delete.assert_awaited_once_with(HEARTBEAT_KEY)

    async def test_tolerates_redis_failure(self):
        """Best-effort: a failure here must not break the graceful shutdown path."""
        with patch.object(
            worker_mod, "get_redis_client", new_callable=AsyncMock, side_effect=ConnectionError("no redis")
        ):
            await worker_mod.clear_heartbeat()  # must not raise


class TestEventListenerState:
    """The heartbeat's event_listener_subscribed flag depends on the listener
    updating the shared state dict — pin that contract."""

    async def test_listener_sets_state_true_on_subscribe_and_false_on_exit(self):
        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.aclose = AsyncMock()

        async def _empty_listen():
            return
            yield {}  # pragma: no cover — makes this an async generator

        pubsub.listen = _empty_listen
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub

        state = {worker_mod.LISTENER_STATE_SUBSCRIBED: False}
        with patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = redis_client
            await worker_mod.source_event_listener(None, state)

        # After the listener exits, the state must be reset so a stale heartbeat
        # does not keep claiming the listener is subscribed.
        assert state[worker_mod.LISTENER_STATE_SUBSCRIBED] is False

    async def test_listener_state_true_while_subscribed(self):
        """While the listen loop is active the state must report subscribed."""
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _blocking_listen():
            entered.set()
            await release.wait()
            yield {}  # pragma: no cover — never reached

        pubsub = MagicMock()
        pubsub.subscribe = AsyncMock()
        pubsub.unsubscribe = AsyncMock()
        pubsub.aclose = AsyncMock()
        pubsub.listen = _blocking_listen
        redis_client = MagicMock()
        redis_client.pubsub.return_value = pubsub

        state = {worker_mod.LISTENER_STATE_SUBSCRIBED: False}
        with patch.object(worker_mod, "get_redis_client", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = redis_client
            task = asyncio.create_task(worker_mod.source_event_listener(None, state))
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert state[worker_mod.LISTENER_STATE_SUBSCRIBED] is True
            release.set()
            await task

        assert state[worker_mod.LISTENER_STATE_SUBSCRIBED] is False


class TestRedisKeysContract:
    def test_key_name_and_ttl(self):
        """The healthcheck (docker compose) hardcodes the key name; lock it here."""
        assert RedisKeys.WORKER_HEARTBEAT == "scheduler:worker:heartbeat"
        assert RedisKeys.worker_heartbeat_key() == "scheduler:worker:heartbeat"
        assert RedisKeys.WORKER_HEARTBEAT_TTL == 45
        assert worker_mod.HEARTBEAT_INTERVAL_SECONDS == 15
