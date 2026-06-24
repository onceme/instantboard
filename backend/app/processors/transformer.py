import logging
import re
import urllib.parse
from datetime import UTC, datetime
from typing import Any

from app.processors.base import BaseProcessor

logger = logging.getLogger(__name__)

TRACKING_PARAMS = [
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_content",
    "utm_term",
    "fbclid",
    "gclid",
    "ref",
    "source",
    "share",
    "mc_cid",
    "mc_eid",
]


class TransformerProcessor(BaseProcessor):
    async def process(self, item: dict, source: Any) -> dict | None:
        transformed = {}

        transformed["title"] = item.get("title", "").strip()
        if not transformed["title"]:
            return None

        summary = item.get("summary", "") or item.get("description", "") or ""
        transformed["summary"] = self._clean_html_summary(summary)

        transformed["url"] = self._normalize_url(item.get("url", ""))
        if not transformed["url"]:
            return None

        transformed["image_url"] = self._normalize_url(item.get("image_url", "") or item.get("thumbnail", ""))

        published_at = item.get("published_at") or item.get("pubDate") or ""
        transformed["published_at"] = self._normalize_timestamp(published_at)

        transformed["fetched_at"] = datetime.now(UTC).isoformat()

        topic_tags = item.get("topic_tags", [])
        if isinstance(topic_tags, list):
            transformed["topic_tags"] = topic_tags
        else:
            transformed["topic_tags"] = []

        transformed["extra_data"] = item.get("extra_data", {}) or {}

        transformed["source_id"] = str(getattr(source, "id", ""))
        transformed["source_name"] = getattr(source, "name", "")
        transformed["category_id"] = item.get("category_id", "")
        transformed["tenant_id"] = str(getattr(source, "tenant_id", "default") or "default")

        transformed["priority"] = self._calculate_priority(item, source)

        transformed["_dedup_hash"] = item.get("_dedup_hash", "")

        return transformed

    def _clean_html_summary(self, summary: str) -> str:
        clean = re.sub(r"<[^>]+>", "", summary)
        clean = re.sub(r"&nbsp;", " ", clean)
        clean = re.sub(r"&amp;", "&", clean)
        clean = re.sub(r"&lt;", "<", clean)
        clean = re.sub(r"&gt;", ">", clean)
        clean = re.sub(r"&quot;", '"', clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) > 200:
            clean = clean[:200] + "..."
        return clean

    def _normalize_url(self, url: str) -> str:
        url = url.strip()
        if not url:
            return ""

        if url and not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        try:
            parsed = urllib.parse.urlparse(url)
            query_dict = urllib.parse.parse_qs(parsed.query)
            for param in TRACKING_PARAMS:
                query_dict.pop(param, None)
            clean_query = urllib.parse.urlencode(query_dict, doseq=True)
            normalized = urllib.parse.urlunparse(
                (
                    parsed.scheme,
                    parsed.netloc.lower(),
                    parsed.path,
                    parsed.params,
                    clean_query,
                    "",
                )
            )
            if normalized.endswith("/"):
                normalized = normalized[:-1]
            return normalized
        except Exception:
            return url

    def _normalize_timestamp(self, timestamp: str) -> str:
        if not timestamp:
            return datetime.now(UTC).isoformat()

        formats = [
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%a, %d %b %Y %H:%M:%S %z",
            "%a, %d %b %Y %H:%M:%S",
            "%d %b %Y %H:%M:%S",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(timestamp, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt.astimezone(UTC).isoformat()
            except ValueError:
                continue

        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC).isoformat()
        except (ValueError, TypeError):
            return datetime.now(UTC).isoformat()

    def _calculate_priority(self, item: dict, source: Any) -> int:
        source_priority = getattr(source, "priority", 5)

        content_length = len(item.get("summary", "") or "")
        length_bonus = 0
        if content_length > 200:
            length_bonus = 1
        if content_length > 500:
            length_bonus = 2

        freshness_bonus = 0
        published_at = item.get("published_at", "")
        if published_at:
            try:
                dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                hours_since = (datetime.now(UTC) - dt).total_seconds() / 3600
                if hours_since < 1:
                    freshness_bonus = 2
                elif hours_since < 6:
                    freshness_bonus = 1
            except (ValueError, TypeError):
                pass

        hn_score = 0
        extra_data = item.get("extra_data", {}) or {}
        if isinstance(extra_data, dict):
            hn_score = extra_data.get("hn_score", 0)
        score_bonus = 0
        if hn_score > 100:
            score_bonus = 2
        elif hn_score > 50:
            score_bonus = 1

        total = source_priority + length_bonus + freshness_bonus + score_bonus
        return min(total, 10)
