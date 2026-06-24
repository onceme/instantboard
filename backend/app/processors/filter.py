import logging
from typing import Any

from app.processors.base import BaseProcessor

logger = logging.getLogger(__name__)

MIN_TITLE_LENGTH = 5
MIN_CONTENT_LENGTH = 20

BLACKLIST_KEYWORDS = [
    "sponsor",
    "sponsored",
    "advertisement",
    "promo",
    "affiliate",
    "click here",
    "buy now",
    "limited offer",
]


class FilterProcessor(BaseProcessor):
    async def process(self, item: dict, source: Any) -> dict | None:
        title = item.get("title", "")
        summary = item.get("summary", "") or ""

        if not title or len(title.strip()) < MIN_TITLE_LENGTH:
            logger.debug(f"Filter: title too short '{title}'")
            return None

        combined_text = f"{title} {summary}".lower()

        for keyword in BLACKLIST_KEYWORDS:
            if keyword.lower() in combined_text:
                logger.debug(f"Filter: blacklisted keyword '{keyword}' in '{title}'")
                return None

        if (not summary or len(summary.strip()) < MIN_CONTENT_LENGTH) and not item.get("extra_data", {}).get(
            "hn_score", 0
        ):
            logger.debug(f"Filter: content too short for '{title}'")
            item["summary"] = title
            return item

        category = getattr(source, "category", None)
        keywords_filter = []
        if category:
            keywords_filter = getattr(category, "keywords_filter", None) or []
        source_keywords = getattr(source, "keywords_filter", None) or []
        if source_keywords:
            keywords_filter = source_keywords

        if not keywords_filter:
            return item

        for keyword in keywords_filter:
            if keyword.lower() in combined_text:
                return item

        logger.debug(f"Filter: item '{title}' did not match any keyword filter")
        return None
