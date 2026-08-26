import html
import logging
import re
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)

_IMAGE_URL_RE = re.compile(r"\.(?:jpe?g|png|gif|webp|avif|bmp)(?:[?#]|$)", re.IGNORECASE)
_IMG_TAG_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _is_image_media(url: str, media_type: str) -> bool:
    if media_type.startswith("image"):
        return True
    if not media_type:
        return bool(_IMAGE_URL_RE.search(url))
    return False


def _first_img_in_html(entry: dict) -> str:
    blobs: list[str] = []
    content = entry.get("content") or []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("value"):
                blobs.append(str(block["value"]))
    for key in ("summary", "description"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            blobs.append(value)
    for blob in blobs:
        match = _IMG_TAG_RE.search(blob)
        if match:
            url = html.unescape(match.group(1)).strip()
            if url:
                return url
    return ""


def _extract_image_url(entry: dict) -> str:
    """First usable image URL for a feed entry, in priority order.

    Sources: media:content → media:thumbnail → enclosure → first <img>
    inside the entry content/summary HTML. Returns "" when nothing usable.
    """
    for media in entry.get("media_content") or []:
        url = (media.get("url") or "").strip()
        if url and _is_image_media(url, media.get("type") or ""):
            return url

    for thumb in entry.get("media_thumbnail") or []:
        url = (thumb.get("url") or "").strip()
        if url:
            return url

    for enc in entry.get("enclosures") or []:
        url = (enc.get("href") or "").strip()
        if url and _is_image_media(url, enc.get("type") or ""):
            return url

    return _first_img_in_html(entry)


class RSSCollector(BaseCollector):
    timeout_seconds = 30
    max_retries = 3
    retry_base_delay_seconds = 1.0

    USER_AGENT = "Mozilla/5.0 (compatible; InstantBoard/1.0; +https://instantboard.github.io)"

    async def fetch_data(self, source: Any) -> Any:
        url = getattr(source, "url", "")
        if not url:
            logger.warning(f"No URL configured for source {getattr(source, 'name', 'unknown')}")
            return None

        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "application/rss+xml, application/xml, application/atom+xml, text/xml, */*",
        }

        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    logger.warning(f"RSS fetch returned {response.status_code} for {url}")
                    raise RuntimeError(f"HTTP {response.status_code} for {url}")
                return response.text
            except httpx.TimeoutException:
                logger.warning(f"RSS timeout for {url}")
                raise
            except httpx.HTTPError as e:
                logger.warning(f"RSS HTTP error for {url}: {e}")
                raise

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

            item["image_url"] = _extract_image_url(entry) or None

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
