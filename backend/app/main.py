import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from starlette.responses import JSONResponse

from app.api.router import v1_router
from app.config import settings
from app.core.middleware import setup_middlewares
from app.core.redis import close_redis, get_redis_client
from app.core.sse_router import event_router
from app.db.init_db import create_tables

logger = logging.getLogger("instantboard")
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

_start_time: datetime | None = None
_sse_listener_task = None
_heartbeat_task = None
_metrics_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _start_time, _sse_listener_task, _heartbeat_task, _metrics_task
    _start_time = datetime.now(UTC)

    logger.info("InstantBoard starting up...")

    logger.info("Ensuring database tables exist...")
    await create_tables()
    logger.info("Database tables ready")

    logger.info("Connecting to Redis...")
    redis_client = await get_redis_client()
    ping = await redis_client.ping()
    if ping:
        logger.info("Redis connected")
    else:
        logger.warning("Redis ping failed, running in degraded mode")

    _sse_listener_task = asyncio.create_task(event_router.start_redis_listener())
    logger.info("SSE Redis Pub/Sub listener started")

    _heartbeat_task = event_router.start_heartbeat(settings.sse_heartbeat_interval)
    logger.info(f"SSE heartbeat started (interval={settings.sse_heartbeat_interval}s)")

    if settings.scheduler_enabled:
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        from app.db.session import async_session_factory
        from app.models.source import Source
        from app.scheduler.manager import scheduler_manager

        await scheduler_manager.start()
        logger.info("Scheduler started")

        async with async_session_factory() as session:
            result = await session.execute(
                select(Source).where(Source.is_active).options(selectinload(Source.category))
            )
            active_sources = result.scalars().all()
            await scheduler_manager.schedule_all_active_sources(active_sources)
            logger.info(f"Scheduled {len(active_sources)} active data sources")
    else:
        logger.info("Scheduler disabled")

    from app.services.dashboard import start_metrics_collection

    _metrics_task = await start_metrics_collection(
        start_time=_start_time,
        tenant_id="00000000-0000-0000-0000-000000000000",
    )
    logger.info("Dashboard metrics collection started")

    yield

    logger.info("InstantBoard shutting down...")

    from app.services.dashboard import stop_metrics_collection

    stop_metrics_collection()

    event_router.stop_heartbeat()
    event_router.stop_redis_listener()

    if _sse_listener_task:
        _sse_listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _sse_listener_task

    if _heartbeat_task:
        _heartbeat_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _heartbeat_task

    if _metrics_task:
        _metrics_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _metrics_task

    if settings.scheduler_enabled:
        from app.scheduler.manager import scheduler_manager

        await scheduler_manager.shutdown(wait=True)
        logger.info("Scheduler shutdown")

    await close_redis()
    logger.info("Redis connection closed")

    logger.info("Shutdown complete")


app = FastAPI(
    title="InstantBoard",
    description="Real-time information aggregation message board",
    version="1.0.0",
    lifespan=lifespan,
)

setup_middlewares(app)

app.include_router(v1_router)


@app.get("/", include_in_schema=False)
async def root():
    uptime = 0
    if _start_time:
        uptime = int((datetime.now(UTC) - _start_time).total_seconds())
    return JSONResponse(
        {
            "name": "InstantBoard",
            "version": "1.0.0",
            "description": "Real-time information aggregation message board",
            "environment": settings.env,
            "uptime_seconds": uptime,
            "api_docs": "/api/v1/docs",
            "timestamp": datetime.now(UTC).isoformat(),
        }
    )
