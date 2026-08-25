from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_db, get_redis, require_admin

router = APIRouter()


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": "1.0.0",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/health/detail")
async def health_detail(
    # Admin-only: exposes PostgreSQL/Redis internals and the environment name.
    # /health (above) stays public for CD smoke tests.
    user: dict = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
    redis_client: Redis = Depends(get_redis),
):
    details = {}

    try:
        from sqlalchemy import text

        await db.execute(text("SELECT 1"))
        details["postgresql"] = {
            "status": "healthy",
            "response_time_ms": None,
        }
    except Exception as e:
        details["postgresql"] = {
            "status": "down",
            "error": str(e),
        }

    try:
        ping_result = await redis_client.ping()
        details["redis"] = {
            "status": "healthy" if ping_result else "down",
        }
    except Exception as e:
        details["redis"] = {
            "status": "down",
            "error": str(e),
        }

    overall_healthy = all(d.get("status") == "healthy" for d in details.values())

    return {
        "status": "healthy" if overall_healthy else "degraded",
        "version": "1.0.0",
        "environment": settings.env,
        "timestamp": datetime.now(UTC).isoformat(),
        "details": details,
    }
