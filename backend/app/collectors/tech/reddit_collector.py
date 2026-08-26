import html
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector

logger = logging.getLogger(__name__)


def _subreddit_list(raw: Any) -> list[str]:
    """Normalize config.subreddits into a clean, de-duplicated subreddit name list.

    Accepts a list (["artificial", "robotics"]) or a "artificial,robotics" /
    "artificial+robotics" style string. Leading "r/" prefixes are stripped.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.replace("+", ",").split(",")
    elif isinstance(raw, (list, tuple)):
        parts = [str(part) for part in raw]
    else:
        return []

    subreddits: list[str] = []
    for part in parts:
        name = str(part).strip().removeprefix("r/").strip()
        if name and name not in subreddits:
            subreddits.append(name)
    return subreddits


class RedditCollector(BaseCollector):
    """Social collector for Reddit via the public JSON listing endpoints.

    No OAuth/credentials are required, but Reddit mandates a descriptive
    User-Agent (config.user_agent, default instantboard-collector/1.0).
    Each subreddit in config.subreddits is fetched individually and posts
    are aggregated with cross-subreddit de-duplication by post id.
    """

    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 1.0
    rate_limit_per_minute = 10

    BASE_URL = "https://www.reddit.com"
    DEFAULT_USER_AGENT = "instantboard-collector/1.0"
    DEFAULT_LIMIT = 25
    MAX_LIMIT = 100
    SELFTEXT_MAX_CHARS = 300

    async def fetch_data(self, source: Any) -> Any:
        config = getattr(source, "config", {}) or {}
        subreddits = _subreddit_list(config.get("subreddits"))
        if not subreddits:
            logger.warning(f"Reddit source {getattr(source, 'name', 'unknown')} has no config.subreddits")
            return None

        try:
            limit = int(config.get("limit") or self.DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = self.DEFAULT_LIMIT
        limit = max(1, min(limit, self.MAX_LIMIT))

        user_agent = str(config.get("user_agent") or self.DEFAULT_USER_AGENT)
        headers = {"User-Agent": user_agent, "Accept": "application/json"}

        posts: list[dict] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            for subreddit in subreddits:
                url = f"{self.BASE_URL}/r/{subreddit}/new.json"
                try:
                    response = await client.get(url, headers=headers, params={"limit": limit})
                except httpx.TimeoutException:
                    logger.warning(f"Reddit timeout fetching r/{subreddit}")
                    raise
                except httpx.HTTPError as e:
                    logger.warning(f"Reddit HTTP error fetching r/{subreddit}: {e}")
                    raise

                if response.status_code == 429:
                    logger.warning(f"Reddit rate limited (429) fetching r/{subreddit}; returning empty result")
                    return []
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code} for {url}")

                try:
                    payload = response.json()
                except ValueError:
                    raise RuntimeError(f"Invalid JSON from {url}") from None

                children = (payload.get("data") or {}).get("children") or []
                for child in children:
                    post = (child or {}).get("data") if isinstance(child, dict) else None
                    if not isinstance(post, dict):
                        continue
                    post_key = str(post.get("name") or post.get("id") or "")
                    if post_key:
                        if post_key in seen:
                            continue
                        seen.add(post_key)
                    posts.append(post)

        return posts

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not raw_data or not isinstance(raw_data, list):
            return []

        items = []
        for post in raw_data:
            if not isinstance(post, dict):
                continue
            title = str(post.get("title") or "").strip()
            if not title:
                continue

            url = str(post.get("url") or "")
            if not url or post.get("is_self"):
                permalink = str(post.get("permalink") or "")
                if permalink:
                    url = f"{self.BASE_URL}{permalink}"

            selftext = str(post.get("selftext") or "").strip()
            if selftext:
                summary = selftext[: self.SELFTEXT_MAX_CHARS]
            else:
                summary = f"Submitted by {post.get('author') or 'unknown'}"

            try:
                created_utc = float(post.get("created_utc") or 0)
                published_at = datetime.fromtimestamp(created_utc, tz=UTC).isoformat() if created_utc > 0 else ""
            except (TypeError, ValueError, OSError, OverflowError):
                published_at = ""

            author = str(post.get("author") or "")
            items.append(
                {
                    "title": title,
                    "url": url,
                    "summary": summary,
                    "published_at": published_at,
                    "author": author,
                    "image_url": self._extract_image_url(post) or None,
                    "extra_data": {
                        "reddit_score": post.get("score", 0),
                        "num_comments": post.get("num_comments", 0),
                        "subreddit": post.get("subreddit", ""),
                        "author": author,
                    },
                }
            )

        return items

    @staticmethod
    def _extract_image_url(post: dict) -> str:
        """Best-effort post thumbnail: preview image first, then thumbnail field.

        Reddit escapes "&" as "&amp;" inside preview URLs; thumbnail holds
        placeholders ("self", "default", "nsfw") for posts without media.
        """
        url = ""
        preview = post.get("preview")
        if isinstance(preview, dict):
            images = preview.get("images") or []
            if images and isinstance(images[0], dict):
                source = images[0].get("source")
                if isinstance(source, dict):
                    url = str(source.get("url") or "").strip()
        if not url:
            thumbnail = str(post.get("thumbnail") or "").strip()
            if thumbnail.startswith(("http://", "https://")):
                url = thumbnail
        return html.unescape(url) if url else ""
