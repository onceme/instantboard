import logging
import time
from urllib.parse import urlparse

from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import settings
from app.core.redis import RedisKeys, get_redis_client
from app.dependencies import get_client_ip
from app.schemas.base import ErrorCode, ErrorDetail, ErrorResponse

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


# Path prefix covered by request validation; anything outside it (nginx /healthz
# probe, frontend static files, the root info endpoint) is never checked.
_API_PATH_PREFIX = "/api/v1/"

# Diagnostic endpoint exempt from the User-Agent requirement: CD smoke tests and
# manual curl-based troubleshooting must work without bespoke headers. Prefix match
# mirrors RequestLoggingMiddleware.SKIP_PATHS semantics (covers /health/detail).
_UA_EXEMPT_PATH_PREFIXES = ("/api/v1/health",)


class RequestValidationMiddleware(BaseHTTPMiddleware):
    """Layer-4 request validation (security.md §3.3): cheap shape checks that
    reject trivially malformed API requests before authentication and routing.

    - User-Agent: /api/v1/* requests must carry a non-blank User-Agent header
      (switch: settings.require_user_agent, default on) — blocks the simplest
      scripts. /api/v1/health is exempt (see _UA_EXEMPT_PATH_PREFIXES); the
      nginx /healthz probe never reaches this middleware because it sits
      outside /api/v1/ and is answered by nginx itself.
    - Body size: requests whose Content-Length exceeds
      settings.max_request_body_bytes (default 10KB) are rejected with 413 —
      blocks oversized payloads. Chunked/streamed requests carry no
      Content-Length header and are intentionally not limited here: stopping
      them early would require buffering the body, and this layer is only a
      cheap pre-filter (real limits belong to nginx / L2 rate limiting).

    Any parsing/validation error inside this middleware fails open with a
    warning log, matching OriginGuardMiddleware's availability-first stance.
    """

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if not path.startswith(_API_PATH_PREFIX):
            return await call_next(request)
        try:
            rejection = self._validate(request, path)
        except Exception as e:
            # Availability first: a parsing hiccup must never lock out
            # legitimate requests (same stance as OriginGuardMiddleware).
            logger.warning(f"Request validation check failed, allowing request: {e}")
            return await call_next(request)
        if rejection is not None:
            return rejection
        return await call_next(request)

    def _validate(self, request: Request, path: str) -> JSONResponse | None:
        if settings.require_user_agent:
            exempt = any(path.startswith(prefix) for prefix in _UA_EXEMPT_PATH_PREFIXES)
            if not exempt:
                user_agent = request.headers.get("user-agent")
                if user_agent is None or not user_agent.strip():
                    return self._rejected(400, "User-Agent header is required")
        content_length = request.headers.get("content-length")
        if content_length is not None and int(content_length) > settings.max_request_body_bytes:
            limit = settings.max_request_body_bytes
            return self._rejected(413, f"Request body must not exceed {limit} bytes")
        return None

    @staticmethod
    def _rejected(status_code: int, message: str) -> JSONResponse:
        envelope = ErrorResponse(error=ErrorDetail(code=ErrorCode.VALIDATION_ERROR, message=message))
        return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


# Methods that mutate state. Only these are origin-checked; GET/HEAD/OPTIONS are
# exempt (SSE is a GET, and OPTIONS preflights are handled by CORSMiddleware).
_STATE_CHANGING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth origin check for state-changing requests.

    Authentication is pure Bearer (no cookies), so CSRF is not possible today —
    this guard instead blocks a cross-site request that somehow carries a valid
    Authorization header (e.g. a stolen token replayed from a malicious page).

    Policy: exact-match the request Origin against settings.cors_origins
    (same list CORS uses, so the two layers never disagree); without an Origin
    header, fall back to the Referer's scheme://host[:port] prefix. Requests
    with neither header pass through: non-browser clients (curl, server-to-server
    collectors, TestClient) legitimately omit them, while every browser-initiated
    cross-site mutation carries at least one of the two.
    """

    async def dispatch(self, request: Request, call_next):
        if request.method in _STATE_CHANGING_METHODS:
            try:
                allowed_origins = settings.cors_origins
                # "*" keeps wildcard semantics consistent with CORS. Note: never use
                # the wildcard in production — it disables this layer entirely.
                if "*" not in allowed_origins:
                    allowed = set(allowed_origins)
                    origin = request.headers.get("origin")
                    if origin is not None:
                        if origin not in allowed:
                            return self._forbidden()
                    else:
                        referer = request.headers.get("referer")
                        if referer is not None:
                            parsed = urlparse(referer)
                            if not parsed.scheme or not parsed.netloc:
                                logger.warning(f"Unparseable Referer header, allowing: {referer!r}")
                            elif f"{parsed.scheme}://{parsed.netloc}" not in allowed:
                                return self._forbidden()
            except Exception as e:
                # Availability first: a parsing/config hiccup must never lock out
                # legitimate writers; the primary CSRF defense is cookie-free auth.
                logger.warning(f"Origin guard check failed, allowing request: {e}")
        return await call_next(request)

    @staticmethod
    def _forbidden() -> JSONResponse:
        envelope = ErrorResponse(error=ErrorDetail(code=ErrorCode.FORBIDDEN, message="Origin not allowed"))
        return JSONResponse(status_code=403, content=envelope.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Layer 3: IP blacklist (security.md §3.3)
# ---------------------------------------------------------------------------

# In-process snapshot of the banned-IP set. Checking Redis (SISMEMBER) on every
# request would add a round trip to every request, so the whole set is pulled
# once per settings.ip_blacklist_cache_ttl window (SMEMBERS) and membership is
# then answered from memory. Admin add/remove endpoints call
# invalidate_ipblacklist_cache() so a change applies to this process
# immediately; with multiple api workers the other processes converge within
# the TTL window.
_ip_blacklist_cache: frozenset[str] | None = None
_ip_blacklist_cache_fetched_at: float = 0.0


def invalidate_ipblacklist_cache() -> None:
    """Drop the in-memory blacklist snapshot; the next request re-reads Redis."""
    global _ip_blacklist_cache, _ip_blacklist_cache_fetched_at
    _ip_blacklist_cache = None
    _ip_blacklist_cache_fetched_at = 0.0


async def _load_ip_blacklist() -> frozenset[str]:
    """Return the banned-IP set, refreshing the snapshot from Redis when stale."""
    global _ip_blacklist_cache, _ip_blacklist_cache_fetched_at
    now = time.monotonic()
    if _ip_blacklist_cache is not None and now - _ip_blacklist_cache_fetched_at < settings.ip_blacklist_cache_ttl:
        return _ip_blacklist_cache
    client = await get_redis_client()
    members = await client.smembers(RedisKeys.IP_BLACKLIST)
    _ip_blacklist_cache = frozenset(members)
    _ip_blacklist_cache_fetched_at = now
    return _ip_blacklist_cache


class IPBlacklistMiddleware(BaseHTTPMiddleware):
    """Layer-3 IP blacklist enforcement (security.md §3.3).

    Registered as the OUTERMOST middleware: banned IPs are rejected before
    RequestLogging, so their requests are neither logged nor counted in the
    request statistics, and they never reach origin validation or routing.

    The client IP is determined with dependencies.get_client_ip — the same
    rightmost-well-formed-hop X-Forwarded-For rule used by the admin-login
    lockout — so a ban cannot be bypassed by forging the left (client
    controlled) side of X-Forwarded-For.

    Fail-open: if the blacklist cannot be read (Redis down, malformed cache
    rebuild) the request passes with a debug log, matching the
    availability-first stance of OriginGuard/RequestValidation. Only manual
    admin bans exist today; automatic banning needs the L2 rate-limit attack
    counters and is deferred until that layer is implemented.
    """

    async def dispatch(self, request: Request, call_next):
        try:
            blocked = await _load_ip_blacklist()
        except Exception as e:
            logger.debug(f"IP blacklist unavailable, failing open: {e}")
            blocked = frozenset()
        # Skip IP extraction entirely while the blacklist is empty.
        if blocked and get_client_ip(request) in blocked:
            return self._forbidden()
        return await call_next(request)

    @staticmethod
    def _forbidden() -> JSONResponse:
        # Deliberately terse: the response must not reveal that an IP
        # blacklist exists or that this address is on it.
        envelope = ErrorResponse(error=ErrorDetail(code=ErrorCode.FORBIDDEN, message="Access denied"))
        return JSONResponse(status_code=403, content=envelope.model_dump(mode="json"))


def setup_middlewares(app):
    setup_cors(app)
    # add_middleware prepends, so the runtime stack order is IPBlacklist ->
    # RequestLogging -> OriginGuard -> RequestValidation -> CORS -> app.
    # The blacklist sits outermost: banned IPs are dropped before anything
    # else happens (not even request logging). Request validation sits after
    # OriginGuard and before CORS: cross-site mutations are rejected first,
    # then malformed requests (missing User-Agent / oversized body), while
    # every rejection still lands inside RequestLogging and is counted in
    # the request statistics.
    app.add_middleware(RequestValidationMiddleware)
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(IPBlacklistMiddleware)
