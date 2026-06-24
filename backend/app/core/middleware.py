import json
import logging
import time
from collections import deque

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.core.redis import RedisKeys, redis_get, redis_hset, redis_set

logger = logging.getLogger("instantboard")

_request_log: deque = deque(maxlen=1000)
_error_count: int = 0
_total_request_count: int = 0
_total_response_time_ms: float = 0.0


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

        if not skip and duration_ms < 60000:
            global _total_request_count, _error_count, _total_response_time_ms

            _total_request_count += 1
            _total_response_time_ms += duration_ms

            if status_code >= 400:
                _error_count += 1

            try:
                minute_key = int(time.time()) // 60

                metrics_key = RedisKeys.SYSTEM_METRICS
                current_metrics = await redis_get(metrics_key)
                metrics_data = {}
                if current_metrics:
                    try:
                        metrics_data = json.loads(current_metrics)
                    except (json.JSONDecodeError, TypeError):
                        metrics_data = {}

                metrics_data["request_count_total"] = _total_request_count
                metrics_data["error_count_total"] = _error_count
                metrics_data["avg_response_time_ms"] = round(_total_response_time_ms / max(_total_request_count, 1), 2)
                metrics_data["error_rate"] = round(_error_count / max(_total_request_count, 1) * 100, 2)

                if _total_request_count % 1000 == 0:
                    avg_ms = round(_total_response_time_ms / _total_request_count, 2)
                    err_rate = round(_error_count / _total_request_count * 100, 2)
                    logger.info(
                        f"Request metrics milestone: {_total_request_count} requests, "
                        f"avg_response={avg_ms}ms, error_rate={err_rate}%"
                    )

                    await redis_hset(
                        metrics_key,
                        mapping={
                            "avg_response_time_ms": str(avg_ms),
                            "error_rate": str(err_rate),
                            "request_count": str(_total_request_count),
                        },
                    )

                    minute_metrics_key = f"dashboard:request_metrics:{minute_key}"
                    await redis_hset(
                        minute_metrics_key,
                        mapping={
                            "avg_response_ms": str(round(duration_ms, 1)),
                            "status_code": str(status_code),
                            "method": request.method,
                            "path": path,
                        },
                    )
                    await redis_set(
                        minute_metrics_key,
                        json.dumps(
                            {
                                "avg_response_ms": round(duration_ms, 1),
                                "status_code": status_code,
                                "method": request.method,
                                "path": path,
                            }
                        ),
                        ex=3600,
                    )

            except Exception as e:
                logger.debug(f"Failed to record request metrics in Redis: {e}")

        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        return response


def setup_middlewares(app):
    setup_cors(app)
    app.add_middleware(RequestLoggingMiddleware)
