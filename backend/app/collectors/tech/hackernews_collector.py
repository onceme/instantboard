import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


class HackerNewsCollector(BaseCollector):
    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 1.0
    rate_limit_per_minute = 30

    BASE_URL = "https://hacker-news.firebaseio.com/v0"
    MAX_ITEMS = 30

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        story_type = config.get("story_type", "topstories")

        try:
            story_ids = await self._fetch_story_ids(story_type)
            if not story_ids:
                return []

            limited_ids = story_ids[: self.MAX_ITEMS]
            items_data = await self._fetch_items_batch(limited_ids)
            return items_data
        except Exception as e:
            logger.warning(f"HackerNews fetch failed: {e}")
            return None

    async def _fetch_story_ids(self, story_type: str) -> list[int]:
        url = f"{self.BASE_URL}/{story_type}.json"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            try:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.warning(f"HackerNews API returned {response.status_code} for story IDs")
                    return []
                return response.json()
            except httpx.TimeoutException:
                logger.warning("HackerNews timeout fetching story IDs")
                return []
            except httpx.HTTPError as e:
                logger.warning(f"HackerNews HTTP error fetching story IDs: {e}")
                return []

    async def _fetch_items_batch(self, item_ids: list[int]) -> list[dict]:
        items = []

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for item_id in item_ids:
                try:
                    url = f"{self.BASE_URL}/item/{item_id}.json"
                    response = await client.get(url)
                    if response.status_code == 200:
                        hn_item = response.json()
                        if hn_item:
                            items.append(hn_item)
                except Exception:
                    continue

        return items

    @staticmethod
    def _query_keywords(source: Any) -> list[str]:
        config = getattr(source, "config", {}) or {}
        raw = str(config.get("query", "") or "").strip()
        if not raw:
            return []
        # hnrss-style query strings survive as-is ("AI+machine+learning"); split on
        # whitespace/plus/comma into individual keywords.
        return [kw.lower() for kw in raw.replace("+", " ").replace(",", " ").split() if kw]

    @staticmethod
    def _matches_keywords(title: str, keywords: list[str]) -> bool:
        if not keywords:
            return True
        lowered = title.lower()
        return any(re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", lowered) for kw in keywords)

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not raw_data or not isinstance(raw_data, list):
            return []

        keywords = self._query_keywords(source)

        items = []
        for hn_item in raw_data:
            if not hn_item or hn_item.get("type") != "story":
                continue

            title = hn_item.get("title", "")
            if keywords and not self._matches_keywords(title, keywords):
                continue
            url = hn_item.get("url", "")
            if not url:
                url = f"https://news.ycombinator.com/item?id={hn_item.get('id', '')}"

            score = hn_item.get("score", 0)
            descendants = hn_item.get("descendants", 0)
            by = hn_item.get("by", "")

            summary = ""
            if hn_item.get("text"):
                summary = hn_item.get("text", "")

            if not summary:
                summary = f"Score: {score} | Comments: {descendants}"

            time_val = hn_item.get("time", 0)
            published_at = datetime.fromtimestamp(time_val, tz=UTC).isoformat() if time_val else ""

            item = {
                "title": title,
                "url": url,
                "summary": summary,
                "published_at": published_at,
                "author": by,
                "extra_data": {
                    "hn_id": hn_item.get("id", ""),
                    "hn_score": score,
                    "hn_comments": descendants,
                    "hn_by": by,
                },
            }

            items.append(item)

        return items
