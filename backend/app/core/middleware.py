import logging
import time

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.core.redis import RedisKeys, get_redis_client

logger = logging.getLogger("instantboard")

# Per-process request counter used solely for the periodic milestone log line. The
# shared request statistics shown on the dashboard live in Redis (written by
# _record_request_stats), so they survive multiple api workers and restarts.
_total_request_count: int = 0
MILESTONE_LOG_EVERY = 1000

# Requests slower than this are excluded from the stats (long polls / stuck
# clients would skew latency averages).
SLOW_REQUEST_THRESHOLD_MS = 60000


def _status_bucket(status_code: int) -> str | None:
    """Minute-bucket hash field for the status class; only 2xx/4xx/5xx are bucketed."""
    if 200 <= status_code < 300:
        return "count_2xx"
    if 400 <= status_code < 500:
        return "count_4xx"
    if 500 <= status_code < 600:
        return "count_5xx"
    return None


async def _record_request_stats(duration_ms: float, status_code: int) -> None:
    """Record one completed request into Redis: single pipeline, single round trip.

    Fire-and-forget by contract — no exception may ever reach the request path.

    Key layout (all under the `dashboard:` prefix, see RedisKeys):
      dashboard:api_metrics:totals           hash   cumulative `requests_total`
                                                    (no TTL; survives api restarts)
      dashboard:api_metrics:minute:{minute}  hash   per-minute bucket with fields
                                                    count / latency_ms (sum) /
                                                    count_2xx / count_4xx /
                                                    count_5xx, TTL 120s (refreshed
                                                    on every write)

    Counters are incremented atomically in Redis, so the statistics stay correct
    across multiple api worker processes. Reader side:
    DashboardService.get_api_request_stats() (GET /dashboard/system `api` group).
    """
    try:
        client = await get_redis_client()
        minute_key = RedisKeys.api_metrics_minute_key(int(time.time()) // 60)

        pipe = client.pipeline(transaction=False)
        pipe.hincrby(RedisKeys.API_METRICS_TOTALS, "requests_total", 1)
        pipe.hincrby(minute_key, "count", 1)
        pipe.hincrbyfloat(minute_key, "latency_ms", duration_ms)
        bucket = _status_bucket(status_code)
        if bucket:
            pipe.hincrby(minute_key, bucket, 1)
        pipe.expire(minute_key, RedisKeys.API_METRICS_MINUTE_TTL)
        await pipe.execute()
    except Exception as e:
        logger.debug(f"Failed to record request metrics in Redis: {e}")


def setup_cors(app):
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Tenant-ID"],
        max_age=3600,
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    SKIP_PATHS = {
        "/api/v1/health",
        "/api/v1/stream",
    }

    async def dispatch(self, request: Request, call_next):
        start_time = time.monotonic()
        request_id = f"{request.method}:{request.url.path}"

        response: Response = await call_next(request)

        duration_ms = (time.monotonic() - start_time) * 1000
        status_code = response.status_code

        logger.info(f"{request_id} - {status_code} - {duration_ms:.1f}ms")

        path = request.url.path
        skip = False
        for skip_path in self.SKIP_PATHS:
            if path.startswith(skip_path):
                skip = True
                break

        if not skip and duration_ms < SLOW_REQUEST_THRESHOLD_MS:
            global _total_request_count
            _total_request_count += 1

            if _total_request_count % MILESTONE_LOG_EVERY == 0:
                logger.info(f"Request metrics milestone: {_total_request_count} requests served by this process")

            await _record_request_stats(duration_ms, status_code)

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        return response


def setup_middlewares(app):
    setup_cors(app)
    app.add_middleware(RequestLoggingMiddleware)
