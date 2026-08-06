"""
Worker entrypoint for the InstantBoard scheduler service.

This module is the standalone entrypoint used by the production Docker worker
container (`python -m app.scheduler.worker`). It initializes the database,
starts the scheduler manager, schedules all active data sources for periodic
collection, and handles graceful shutdown on SIGTERM/SIGINT.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.redis import close_redis, get_redis_client
from app.db.init_db import create_tables
from app.db.session import async_session_factory
from app.models.source import Source
from app.scheduler.manager import scheduler_manager

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("instantboard.worker")


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
