import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from app.collectors.base import BaseCollector
from app.config import settings

logger = logging.getLogger(__name__)


def _query_list(raw: Any) -> list[str]:
    """Normalize config.query into a clean, de-duplicated search query list.

    Accepts a list (["AI", "robotics"]) or a comma-separated string
    ("AI OR robotics, space launch"). Commas are the only split character:
    whitespace and Twitter operators (OR, lang:en, -is:retweet, ...) must
    survive inside each individual query.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        parts = [str(part) for part in raw]
    else:
        return []

    queries: list[str] = []
    for part in parts:
        query = str(part).strip()
        if query and query not in queries:
            queries.append(query)
    return queries


def _parse_created_at(value: Any) -> str:
    if not value or not isinstance(value, str):
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


class TwitterCollector(BaseCollector):
    """Social collector for Twitter/X via the API v2 recent-search endpoint.

    Requires a bearer token (settings.twitter_bearer_token, env
    TWITTER_BEARER_TOKEN); the recent-search endpoint only exists on paid API
    tiers, so without a token the fetch logs a warning and returns None
    (treated as a collection failure upstream). Each query in config.query
    (list, or comma-separated string) is fetched individually and tweets are
    merged with cross-query de-duplication by tweet id. 429/401/403 responses
    are logged and mapped to an empty result (no collector failure).
    """

    timeout_seconds = 10
    max_retries = 3
    retry_base_delay_seconds = 2.0
    rate_limit_per_minute = 5

    BASE_URL = "https://api.twitter.com/2"
    DEFAULT_MAX_RESULTS = 20
    MIN_MAX_RESULTS = 10
    MAX_MAX_RESULTS = 100
    TITLE_MAX_CHARS = 200
    TWEET_FIELDS = "created_at,public_metrics"
    METRIC_KEYS = ("like_count", "retweet_count", "reply_count", "impression_count")

    async def fetch_data(self, source: Any) -> Any:
        token = settings.twitter_bearer_token
        if not token:
            logger.warning(f"Twitter source {getattr(source, 'name', 'unknown')}: no bearer token configured")
            return None

        config = getattr(source, "config", {}) or {}
        queries = _query_list(config.get("query"))
        if not queries:
            logger.warning(f"Twitter source {getattr(source, 'name', 'unknown')} has no config.query")
            return None

        try:
            max_results = int(config.get("max_results") or self.DEFAULT_MAX_RESULTS)
        except (TypeError, ValueError):
            max_results = self.DEFAULT_MAX_RESULTS
        max_results = max(self.MIN_MAX_RESULTS, min(max_results, self.MAX_MAX_RESULTS))

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        url = f"{self.BASE_URL}/tweets/search/recent"

        tweets: list[dict] = []
        seen: set[str] = set()

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for query in queries:
                params = {"query": query, "max_results": max_results, "tweet.fields": self.TWEET_FIELDS}
                try:
                    response = await client.get(url, headers=headers, params=params)
                except httpx.TimeoutException:
                    logger.warning(f"Twitter timeout fetching query {query!r}")
                    raise
                except httpx.HTTPError as e:
                    logger.warning(f"Twitter HTTP error fetching query {query!r}: {e}")
                    raise

                if response.status_code == 429:
                    logger.warning(f"Twitter rate limited (429) fetching query {query!r}; returning empty result")
                    return []
                if response.status_code in (401, 403):
                    logger.warning(
                        f"Twitter auth error ({response.status_code}) fetching query {query!r}; "
                        "check TWITTER_BEARER_TOKEN; returning empty result"
                    )
                    return []
                if response.status_code != 200:
                    raise RuntimeError(f"HTTP {response.status_code} for {url}")

                try:
                    payload = response.json()
                except ValueError:
                    raise RuntimeError(f"Invalid JSON from {url}") from None

                for tweet in payload.get("data") or []:
                    if not isinstance(tweet, dict):
                        continue
                    tweet_id = str(tweet.get("id") or "")
                    if tweet_id:
                        if tweet_id in seen:
                            continue
                        seen.add(tweet_id)
                    tweets.append(tweet)

        return tweets

    async def parse_data(self, raw_data: Any, source: Any) -> list[dict]:
        if not raw_data or not isinstance(raw_data, list):
            return []

        items = []
        for tweet in raw_data:
            if not isinstance(tweet, dict):
                continue
            text = str(tweet.get("text") or "").strip()
            if not text:
                continue
            tweet_id = str(tweet.get("id") or "")
            if not tweet_id:
                continue

            metrics = tweet.get("public_metrics")
            extra_data: dict[str, Any] = {}
            if isinstance(metrics, dict):
                extra_data = {key: metrics[key] for key in self.METRIC_KEYS if key in metrics}

            items.append(
                {
                    "title": text[: self.TITLE_MAX_CHARS],
                    "url": f"https://twitter.com/i/status/{tweet_id}",
                    "summary": text,
                    "published_at": _parse_created_at(tweet.get("created_at")),
                    "extra_data": extra_data,
                }
            )

        return items
