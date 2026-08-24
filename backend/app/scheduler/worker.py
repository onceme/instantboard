"""
Worker entrypoint for the InstantBoard scheduler service.

This module is the standalone entrypoint used by the production Docker worker
container (`python -m app.scheduler.worker`). It initializes the database,
starts the scheduler manager, schedules all active data sources for periodic
collection, subscribes to runtime source enable/disable events, and handles
graceful shutdown on SIGTERM/SIGINT.
"""

import asyncio
import contextlib
import json
import logging
import os
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.redis import RedisKeys, close_redis, get_redis_client
from app.db.init_db import create_tables
from app.db.session import async_session_factory
from app.models.source import Source
from app.scheduler.manager import scheduler_manager

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("instantboard.worker")

# Source lifecycle events published by SourceService on the dashboard channel.
# Other dashboard-channel events (source_created, source_health_update, ...) are
# meant for SSE clients and are ignored here.
SOURCE_EVENT_NAMES = {"source_enabled", "source_disabled", "source_deleted"}

# Heartbeat cadence: the worker rewrites its heartbeat every 15s with a TTL of
# 3x that interval (RedisKeys.WORKER_HEARTBEAT_TTL = 45s). The api side treats
# a heartbeat younger than 45s as a live worker. Contract:
# docs/design/infrastructure.md §3.5 "Worker 心跳机制".
HEARTBEAT_INTERVAL_SECONDS = 15

# Mutable state shared with the source event listener: the heartbeat reports
# whether the Redis Pub/Sub listener is currently subscribed, so a silent
# listener death is visible on the dashboard.
LISTENER_STATE_SUBSCRIBED = "subscribed"


async def _load_source_payload(source_id: str) -> dict | None:
    # DB fallback for enable events whose payload is missing source fields
    # (defensive only — the publisher always sends the full source dict).
    async with async_session_factory() as session:
        result = await session.execute(
            select(Source).where(Source.id == source_id).options(selectinload(Source.category))
        )
        source = result.scalar_one_or_none()
        if source is None or not source.is_active:
            return None
        return {
            "id": str(source.id),
            "category_slug": source.category.slug if source.category else "",
            "refresh_interval_seconds": source.refresh_interval_seconds,
            "source_type": source.source_type,
        }


async def handle_source_status_event(event_data: dict) -> None:
    """Map a dashboard-channel source lifecycle event to a scheduler change.

    Semantics: events are incremental hints only. Events published while the
    worker is down (or before it subscribes) are lost by design — Redis Pub/Sub
    is fire-and-forget. The full rebuild from the DB on worker startup
    (schedule_all_active_sources) is the authoritative fallback and self-heals
    any lost event.
    """
    event = event_data.get("event")

    if event == "source_enabled":
        source = event_data.get("source") or {}
        source_id = str(source.get("id") or event_data.get("source_id") or "")
        if not source_id:
            logger.warning("source_enabled event without source id, ignoring")
            return
        if not source.get("id"):
            source = await _load_source_payload(source_id)
            if source is None:
                logger.warning(f"source_enabled: source {source_id} not found or inactive in DB, ignoring")
                return
        await scheduler_manager.add_source_job(source)
        logger.info(f"Runtime scheduling: added collection job for source {source_id}")

    elif event in ("source_disabled", "source_deleted"):
        source_id = str(event_data.get("source_id") or "")
        if not source_id:
            logger.warning(f"{event} event without source id, ignoring")
            return
        await scheduler_manager.remove_source_job(source_id)
        logger.info(f"Runtime scheduling: removed collection job for source {source_id}")


async def source_event_listener(subscribed: asyncio.Event | None = None, listener_state: dict | None = None) -> None:
    """Subscribe to the dashboard channel and apply source enable/disable events.

    Runs for the lifetime of the worker; exits on cancellation or when the Redis
    connection breaks (runtime scheduling then only applies after a worker restart,
    which rebuilds all jobs from the DB anyway). When listener_state is provided,
    listener_state["subscribed"] mirrors the listener liveness for the heartbeat.
    """
    channel = RedisKeys.channel_key("dashboard")
    pubsub = None
    try:
        redis_client = await get_redis_client()
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        logger.info(f"Worker subscribed to source status events on '{channel}'")
        if listener_state is not None:
            listener_state[LISTENER_STATE_SUBSCRIBED] = True
        if subscribed is not None:
            subscribed.set()

        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                data = json.loads(message.get("data"))
            except (TypeError, json.JSONDecodeError):
                logger.warning("Invalid JSON in source status message, ignoring")
                continue
            if not isinstance(data, dict) or data.get("event") not in SOURCE_EVENT_NAMES:
                continue
            try:
                await handle_source_status_event(data)
            except Exception as exc:
                logger.error(f"Failed to handle source status event {data.get('event')}: {exc}")
    except asyncio.CancelledError:
        logger.info("Source status listener cancelled")
        raise
    except Exception as exc:
        logger.warning(f"Source status listener stopped: {exc}. Runtime scheduling requires a worker restart.")
        if subscribed is not None:
            subscribed.set()
    finally:
        # Reflect listener death (Redis connection broken, or shutdown) in the
        # heartbeat so the dashboard can tell event-driven scheduling is blind.
        if listener_state is not None:
            listener_state[LISTENER_STATE_SUBSCRIBED] = False
        if pubsub is not None:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()


async def build_heartbeat_payload(listener_state: dict | None) -> dict:
    """Snapshot the worker's observable state for one heartbeat write.

    The payload is the only window the api process has into this worker
    (prod runs with the api-embedded scheduler disabled), so it carries
    scheduler liveness, job counts and the event-listener state.
    """
    jobs_status = await scheduler_manager.get_jobs_status()
    running_jobs = [j for j in jobs_status if not j.get("pending", False)]
    paused_jobs = [j for j in jobs_status if j.get("pending", False)]
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
        "scheduler_running": bool(scheduler_manager._running),
        "jobs_total": len(jobs_status),
        "jobs_running": len(running_jobs),
        "jobs_paused": len(paused_jobs),
        "event_listener_subscribed": bool(listener_state.get(LISTENER_STATE_SUBSCRIBED) if listener_state else False),
    }


async def write_heartbeat(listener_state: dict | None = None) -> None:
    """Write a single heartbeat to Redis.

    Never raises: a heartbeat failure must only be logged, never kill the
    worker process. The key expires by itself (TTL), so a crashed worker
    naturally turns stale and is then reported down by the api side.
    """
    try:
        payload = await build_heartbeat_payload(listener_state)
        client = await get_redis_client()
        await client.set(
            RedisKeys.worker_heartbeat_key(),
            json.dumps(payload),
            ex=RedisKeys.WORKER_HEARTBEAT_TTL,
        )
    except Exception as exc:  # noqa: BLE001 - heartbeat must never kill the worker
        logger.warning(f"Failed to write worker heartbeat: {exc}")


async def heartbeat_loop(listener_state: dict | None = None) -> None:
    """Background loop that refreshes the heartbeat every HEARTBEAT_INTERVAL_SECONDS.

    write_heartbeat already swallows its own errors; the extra guard here keeps
    the loop alive even if that contract ever changes — a heartbeat task that
    dies silently would turn a healthy worker into a phantom "down" on the
    dashboard.
    """
    while True:
        try:
            await write_heartbeat(listener_state)
        except Exception as exc:  # noqa: BLE001 - keep heartbeating no matter what
            logger.warning(f"Heartbeat loop error: {exc}")
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def clear_heartbeat() -> None:
    """Best-effort removal of the heartbeat on graceful shutdown.

    Deliberately swallows errors: if Redis is already gone the key expires
    on its own within the TTL window.
    """
    try:
        client = await get_redis_client()
        await client.delete(RedisKeys.worker_heartbeat_key())
        logger.info("Worker heartbeat key cleared")
    except Exception as exc:  # noqa: BLE001 - best effort only
        logger.warning(f"Failed to clear worker heartbeat: {exc}")


async def main() -> None:
    logger.info("InstantBoard worker starting...")

    logger.info("Ensuring database tables exist...")
    await create_tables()
    logger.info("Database tables ready")

    logger.info("Connecting to Redis...")
    try:
        redis_client = await get_redis_client()
        ping = await redis_client.ping()
        if ping:
            logger.info("Redis connected")
        else:
            logger.warning("Redis ping failed, worker may operate in degraded mode")
    except Exception as exc:
        logger.warning(f"Redis connection failed: {exc}. Worker continuing without Redis.")

    # The worker process has no SSE connections (the connection registry lives in the api
    # process, core/sse_router). Adaptive pause must be disabled, otherwise every source
    # would be paused permanently after its first collection round due to "no subscribers".
    scheduler_manager.disable_adaptive_pause()

    await scheduler_manager.start()
    logger.info("Scheduler started")

    # Subscribe to source enable/disable events BEFORE the initial DB snapshot so that
    # enables/disables happening during startup are not lost. Duplicates are harmless:
    # re-adding an existing job only reschedules it.
    subscribed = asyncio.Event()
    listener_state = {LISTENER_STATE_SUBSCRIBED: False}
    listener_task = asyncio.create_task(source_event_listener(subscribed, listener_state))
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=5.0)
    except TimeoutError:
        logger.warning("Source event listener not subscribed within 5s, continuing with startup")

    # Publish a heartbeat so the api process (whose embedded scheduler is disabled in
    # prod) can report worker health. Write one immediately, then every interval.
    heartbeat_task = asyncio.create_task(heartbeat_loop(listener_state))

    async with async_session_factory() as session:
        result = await session.execute(select(Source).where(Source.is_active).options(selectinload(Source.category)))
        active_sources = result.scalars().all()
        logger.info(f"Found {len(active_sources)} active data sources")

    await scheduler_manager.schedule_all_active_sources(active_sources)
    logger.info(f"Scheduled {len(active_sources)} active data sources for collection")

    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Received shutdown signal")
        stop_event.set()

    loop = asyncio.get_running_loop()
    import signal

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info("Worker is running. Waiting for shutdown signal...")
    await stop_event.wait()

    # Shutdown order: stop the event listener first (heartbeat keeps running while
    # the listener is torn down so the worker stays observable), then stop the
    # heartbeat loop and delete the key so a graceful shutdown reads as "never
    # started" instead of "stale" on the api side.
    listener_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await listener_task

    heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await heartbeat_task
    await clear_heartbeat()


async def shutdown() -> None:
    logger.info("Worker shutting down...")
    await scheduler_manager.shutdown(wait=True)
    logger.info("Scheduler shutdown complete")
    await close_redis()
    logger.info("Redis connection closed")
    logger.info("Worker shutdown complete")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        asyncio.run(shutdown())
