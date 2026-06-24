import abc
import asyncio
import hashlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from app.core.redis import (
    RedisKeys,
    redis_get,
    redis_set,
)

logger = logging.getLogger(__name__)


class CollectionResult:
    def __init__(
        self,
        items: list[dict],
        success: bool,
        response_time_ms: int = 0,
        error: str | None = None,
        source_id: str = "",
    ):
        self.items = items
        self.success = success
        self.response_time_ms = response_time_ms
        self.error = error
        self.source_id = source_id


class BaseCollector(abc.ABC):
    max_retries: int = 3
    retry_base_delay_seconds: float = 1.0
    timeout_seconds: int = 10
    rate_limit_per_minute: int = 0

    async def collect(self, source: Any) -> CollectionResult:
        start_time = time.monotonic()
        source_id = str(getattr(source, "id", "unknown"))

        rate_ok = await self.rate_limit_check(source)
        if not rate_ok:
            logger.warning(f"Rate limit exceeded for source {source.name}")
            return CollectionResult(
                items=[],
                success=False,
                response_time_ms=0,
                error="Rate limit exceeded",
                source_id=source_id,
            )

        raw_data = await self._collect_with_retry(source)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        if raw_data is None:
            await self.record_health(source, False, elapsed_ms, "All retry attempts failed")
            return CollectionResult(
                items=[],
                success=False,
                response_time_ms=elapsed_ms,
                error="All retry attempts failed",
                source_id=source_id,
            )

        try:
            parsed = await self.parse_data(raw_data, source)
        except Exception as e:
            logger.error(f"Parse error for source {source.name}: {e}")
            await self.record_health(source, False, elapsed_ms, str(e))
            return CollectionResult(
                items=[],
                success=False,
                response_time_ms=elapsed_ms,
                error=str(e),
                source_id=source_id,
            )

        try:
            validated = await self.validate_data(parsed, source)
        except Exception as e:
            logger.error(f"Validation error for source {source.name}: {e}")
            await self.record_health(source, False, elapsed_ms, str(e))
            return CollectionResult(
                items=[],
                success=False,
                response_time_ms=elapsed_ms,
                error=str(e),
                source_id=source_id,
            )

        await self.record_health(source, True, elapsed_ms)
        return CollectionResult(
            items=validated,
            success=True,
            response_time_ms=elapsed_ms,
            source_id=source_id,
        )

    async def _collect_with_retry(self, source: Any) -> Any | None:
        for attempt in range(1, self.max_retries + 1):
            try:
                raw_data = await self.fetch_data(source)
                return raw_data
            except Exception as e:
                logger.warning(
                    f"Collect attempt {attempt}/{self.max_retries} failed "
                    f"for source {getattr(source, 'name', 'unknown')}: {e}"
                )
                if attempt < self.max_retries:
                    delay = self.retry_base_delay_seconds * (2 ** (attempt - 1))
                    await asyncio.sleep(delay)
        logger.error(f"All {self.max_retries} attempts failed for source {getattr(source, 'name', 'unknown')}")
        return None

    async def rate_limit_check(self, source: Any) -> bool:
        if self.rate_limit_per_minute <= 0:
            return True

        source_id = str(getattr(source, "id", "unknown"))
        rate_key = f"rate_limit:collector:{source_id}"

        try:
            current = await redis_get(rate_key)
            if current is None:
                await redis_set(rate_key, "1", ex=60)
                return True
            if int(current) >= self.rate_limit_per_minute:
                return False
            count = int(current) + 1
            await redis_set(rate_key, str(count), ex=60)
            return True
        except Exception:
            logger.warning("Rate limit check: Redis unavailable, skipping rate limit")
            return True

    async def record_health(
        self,
        source: Any,
        success: bool,
        response_time_ms: int,
        error: str | None = None,
    ) -> None:
        source_id = str(getattr(source, "id", "unknown"))
        health_key = RedisKeys.source_health_key(source_id)

        try:
            existing = await redis_get(health_key)
            if existing:
                health_data = eval(existing) if isinstance(existing, str) else existing
            else:
                health_data = {
                    "status": "healthy",
                    "consecutive_failures": 0,
                    "total_fetches_24h": 0,
                    "success_count_24h": 0,
                    "avg_response_time_ms": 0,
                }

            health_data["total_fetches_24h"] = health_data.get("total_fetches_24h", 0) + 1
            now_iso = datetime.now(UTC).isoformat()

            if success:
                health_data["success_count_24h"] = health_data.get("success_count_24h", 0) + 1
                health_data["consecutive_failures"] = 0
                health_data["last_success_at"] = now_iso
                if health_data.get("consecutive_failures", 0) == 0:
                    health_data["status"] = "healthy"
            else:
                health_data["consecutive_failures"] = health_data.get("consecutive_failures", 0) + 1
                health_data["last_failure_at"] = now_iso
                health_data["last_error_message"] = error
                if health_data["consecutive_failures"] >= 10:
                    health_data["status"] = "down"
                elif health_data["consecutive_failures"] >= 3:
                    health_data["status"] = "degraded"

            prev_avg = health_data.get("avg_response_time_ms", 0)
            total = health_data.get("total_fetches_24h", 1)
            health_data["avg_response_time_ms"] = int((prev_avg * (total - 1) + response_time_ms) / total)

            import json

            await redis_set(health_key, json.dumps(health_data), ex=300)
        except Exception as e:
            logger.warning(f"Failed to record health for source {source_id}: {e}")

    @abc.abstractmethod
    async def fetch_data(self, source: Any) -> Any: ...

    @abc.abstractmethod
    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]: ...

    async def validate_data(self, items: list[dict], source: Any) -> list[dict]:
        required_fields = ["title", "url"]
        validated = []
        for item in items:
            if all(item.get(f) for f in required_fields):
                validated.append(item)
            else:
                missing = [f for f in required_fields if not item.get(f)]
                logger.debug(f"Skipping item missing fields {missing}: {item.get('url', 'unknown')}")
        return validated

    def _content_hash(self, title: str, url: str) -> str:
        content = f"{title}:{url}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()
