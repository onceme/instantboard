import logging
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class RSSCollector(BaseCollector):
    timeout_seconds = 15
    max_retries = 3
    retry_base_delay_seconds = 1.0

    async def fetch_data(self, source: Any) -> Any:
        url = getattr(source, "url", "")
        if not url:
            logger.warning(f"No URL configured for source {getattr(source, 'name', 'unknown')}")
            return None

        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            try:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.warning(f"RSS fetch returned {response.status_code} for {url}")
                    return None
                return response.text
            except httpx.TimeoutException:
                logger.warning(f"RSS timeout for {url}")
                return None
            except httpx.HTTPError as e:
                logger.warning(f"RSS HTTP error for {url}: {e}")
                return None

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not raw_data:
            return []

        try:
            feed = feedparser.parse(raw_data)
        except Exception as e:
            logger.warning(f"feedparser parse error for {getattr(source, 'name', 'unknown')}: {e}")
            return []

        if not feed.entries:
            logger.info(f"No entries in RSS feed for {getattr(source, 'name', 'unknown')}")
            return []

        config = getattr(source, "config", {}) or {}
        parse_rules = config.get("parse_rules", {})

        items = []
        for entry in feed.entries:
            item = {
                "title": entry.get("title", ""),
                "url": entry.get("link", ""),
                "published_at": _parse_feedparser_date(entry),
                "author": entry.get("author", ""),
            }

            summary_rule = parse_rules.get("summary", "summary")
            if summary_rule == "summary":
                item["summary"] = entry.get("summary", "")
            elif summary_rule == "description":
                item["summary"] = entry.get("description", "")
            elif summary_rule == "comments_text" or summary_rule == "abstract":
                item["summary"] = entry.get("summary", "") or entry.get("description", "")
            elif summary_rule == "excerpt":
                item["summary"] = entry.get("summary", "")
            else:
                item["summary"] = entry.get("summary", "")

            if not item["summary"]:
                item["summary"] = entry.get("description", "")

            extra_rules = parse_rules.get("extra", {})
            extra_data = {}
            for extra_key, entry_key in extra_rules.items():
                extra_data[extra_key] = entry.get(entry_key, "")
            item["extra_data"] = extra_data

            if entry.get("media_content"):
                for media in entry.get("media_content", []):
                    if media.get("type", "").startswith("image"):
                        item["image_url"] = media.get("url", "")
                        break

            if not item.get("image_url") and entry.get("enclosures"):
                for enc in entry.get("enclosures", []):
                    if enc.get("type", "").startswith("image"):
                        item["image_url"] = enc.get("href", "")
                        break

            items.append(item)

        return items


def _parse_feedparser_date(entry: dict) -> str:
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        try:
            dt = datetime(
                published.tm_year,
                published.tm_mon,
                published.tm_mday,
                published.tm_hour,
                published.tm_min,
                published.tm_sec,
                tzinfo=UTC,
            )
            return dt.isoformat()
        except Exception:
            pass

    published_str = entry.get("published") or entry.get("updated") or ""
    if published_str:
        try:
            dt = datetime.fromisoformat(published_str.replace("Z", "+00:00"))
            return dt.astimezone(UTC).isoformat()
        except (ValueError, TypeError):
            pass

    return datetime.now(UTC).isoformat()
