import hashlib
import logging
from typing import Any

from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import RedisKeys, redis_sadd, redis_sismember
from app.processors.base import BaseProcessor

logger = logging.getLogger(__name__)

# Dedup sets grow with every unseen item; expire them 24h after the last write
# (original design) so memory stays bounded without resurrecting stale entries.
DEDUP_TTL_SECONDS = 86400


class DedupProcessor(BaseProcessor):
    async def process(self, item: dict, source: Any) -> dict | None:
        # Tenant ids are UUID strings across the pipeline (SSE routing, DB columns):
        # fall back to str(SYSTEM_TENANT_ID), never a literal like "default". Unreachable
        # in practice (Source.tenant_id is NOT NULL).
        tenant_id = getattr(source, "tenant_id", str(SYSTEM_TENANT_ID)) or str(SYSTEM_TENANT_ID)
        source_id = str(getattr(source, "id", "unknown"))

        title = item.get("title", "")
        url = item.get("url", "")
        content_hash = self._content_hash(title, url)

        dedup_key = RedisKeys.dedup_key(tenant_id, source_id)
        dedup_member = content_hash

        try:
            is_duplicate = await redis_sismember(dedup_key, dedup_member)
            if is_duplicate:
                logger.debug(f"Dedup: skipping duplicate item '{title}' (hash={content_hash})")
                return None

            await redis_sadd(dedup_key, dedup_member, ttl=DEDUP_TTL_SECONDS)
        except Exception as e:
            logger.warning(f"Redis dedup check failed, proceeding without dedup: {e}")

        item["_dedup_hash"] = content_hash
        return item

    def _content_hash(self, title: str, url: str) -> str:
        content = f"{title}:{url}"
        return hashlib.md5(content.encode("utf-8")).hexdigest()
