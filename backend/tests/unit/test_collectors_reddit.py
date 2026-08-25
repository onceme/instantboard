"""Unit tests for app/collectors/tech/reddit_collector.py."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.collectors import COLLECTOR_REGISTRY, get_collector, resolve_collector
from app.collectors.tech.reddit_collector import RedditCollector, _subreddit_list


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "source-id")
    src.name = kwargs.get("name", "Test Source")
    src.url = kwargs.get("url", "https://example.com")
    src.config = kwargs.get("config", {})
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    src.category = kwargs.get("category", MagicMock())
    return src


def _mock_response(status_code: int = 200, json_data=None, invalid_json: bool = False):
    resp = MagicMock()
    resp.status_code = status_code
    if invalid_json:
        resp.json = MagicMock(side_effect=ValueError("invalid json"))
    else:
        resp.json = MagicMock(return_value=json_data)
    return resp


def _mock_http_client(*responses):
    """Mock httpx.AsyncClient. Repeats a single response; pops through a sequence.

    Requests are recorded in ``client.calls`` as (url, headers, params) tuples.
    """
    client = AsyncMock()
    seq = list(responses)
    calls: list[tuple[str, dict | None, dict | None]] = []

    async def mock_get(url, headers=None, params=None):
        calls.append((url, headers, params))
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    client.get = mock_get
    client.calls = calls
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _make_post(**kwargs):
    post = {
        "id": "abc123",
        "name": "t3_abc123",
        "title": "Test post",
        "author": "tester",
        "subreddit": "artificial",
        "score": 42,
        "num_comments": 7,
        "created_utc": 1700000000,
        "is_self": False,
        "url": "https://example.com/article",
        "permalink": "/r/artificial/comments/abc123/test_post/",
        "selftext": "",
    }
    post.update(kwargs)
    return post


def _listing_payload(posts):
    return {"data": {"children": [{"kind": "t3", "data": p} for p in posts]}}


# ── Registry / resolve_collector ─────────────────────────────────
class TestRedditRegistry:
    def test_registered_in_collector_registry(self):
        assert "reddit" in COLLECTOR_REGISTRY
        assert get_collector("reddit") is RedditCollector

    def test_resolve_collector_social_with_reddit_library(self):
        assert resolve_collector("social", {"library": "reddit"}) is RedditCollector
        assert resolve_collector("social", {"library": "reddit", "subreddits": ["space"]}) is RedditCollector

    def test_resolve_collector_social_without_library(self):
        assert resolve_collector("social", None) is None
        assert resolve_collector("social", {}) is None
        assert resolve_collector("social", {"platform": "reddit"}) is None

    def test_resolve_collector_social_unknown_library(self):
        assert resolve_collector("social", {"library": "nonexistent"}) is None


# ── config.subreddits normalization ──────────────────────────────
class TestSubredditList:
    def test_list_input(self):
        assert _subreddit_list(["artificial", "robotics"]) == ["artificial", "robotics"]

    def test_string_input_comma_and_plus(self):
        assert _subreddit_list("artificial,robotics") == ["artificial", "robotics"]
        assert _subreddit_list("artificial+robotics+embedded") == ["artificial", "robotics", "embedded"]

    def test_strips_r_prefix_and_whitespace(self):
        assert _subreddit_list([" r/artificial ", "space"]) == ["artificial", "space"]

    def test_dedupes_preserving_order(self):
        assert _subreddit_list(["artificial", "artificial", "space"]) == ["artificial", "space"]

    def test_empty_and_invalid_input(self):
        assert _subreddit_list(None) == []
        assert _subreddit_list("") == []
        assert _subreddit_list(" , ,") == []
        assert _subreddit_list(12345) == []


# ── fetch_data ────────────────────────────────────────────────────
class TestRedditFetch:
    async def test_fetch_single_subreddit_success(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["artificial"]})
        payload = _listing_payload([_make_post()])
        mock_client = _mock_http_client(_mock_response(200, payload))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["title"] == "Test post"
        url, headers, params = mock_client.calls[0]
        assert url == "https://www.reddit.com/r/artificial/new.json"
        assert headers["User-Agent"] == RedditCollector.DEFAULT_USER_AGENT
        assert params == {"limit": RedditCollector.DEFAULT_LIMIT}

    async def test_fetch_custom_user_agent_and_limit(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"], "user_agent": "my-bot/2.0", "limit": 42})
        mock_client = _mock_http_client(_mock_response(200, _listing_payload([])))
        with patch("httpx.AsyncClient", return_value=mock_client):
            await c.fetch_data(source)

        _, headers, params = mock_client.calls[0]
        assert headers["User-Agent"] == "my-bot/2.0"
        assert params == {"limit": 42}

    async def test_fetch_limit_clamped_and_invalid_falls_back(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"], "limit": 500})
        mock_client = _mock_http_client(_mock_response(200, _listing_payload([])))
        with patch("httpx.AsyncClient", return_value=mock_client):
            await c.fetch_data(source)
        assert mock_client.calls[0][2] == {"limit": 100}

        source = _make_source(config={"subreddits": ["space"], "limit": "not-a-number"})
        mock_client = _mock_http_client(_mock_response(200, _listing_payload([])))
        with patch("httpx.AsyncClient", return_value=mock_client):
            await c.fetch_data(source)
        assert mock_client.calls[0][2] == {"limit": RedditCollector.DEFAULT_LIMIT}

    async def test_fetch_multiple_subreddits_aggregates_and_dedupes(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["artificial", "robotics"]})
        shared_post = _make_post(name="t3_shared", id="shared", subreddit="artificial")
        first = _listing_payload([_make_post(name="t3_one", id="one"), shared_post])
        second = _listing_payload([shared_post, _make_post(name="t3_two", id="two", subreddit="robotics")])
        mock_client = _mock_http_client(_mock_response(200, first), _mock_response(200, second))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert [p["name"] for p in result] == ["t3_one", "t3_shared", "t3_two"]
        assert mock_client.calls[0][0] == "https://www.reddit.com/r/artificial/new.json"
        assert mock_client.calls[1][0] == "https://www.reddit.com/r/robotics/new.json"

    async def test_fetch_429_returns_empty_and_stops(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["artificial", "robotics"]})
        mock_client = _mock_http_client(_mock_response(429, {}), _mock_response(200, _listing_payload([])))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result == []
        # second subreddit must not be fetched once rate limited
        assert len(mock_client.calls) == 1

    async def test_fetch_subsequent_subreddit_429_returns_empty(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["artificial", "robotics"]})
        ok = _mock_response(200, _listing_payload([_make_post()]))
        mock_client = _mock_http_client(ok, _mock_response(429, {}))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result == []
        assert len(mock_client.calls) == 2

    async def test_fetch_non_200_raises(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        mock_client = _mock_http_client(_mock_response(500, {}))
        with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(RuntimeError, match="HTTP 500"):
            await c.fetch_data(source)

    async def test_fetch_invalid_json_raises(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        mock_client = _mock_http_client(_mock_response(200, invalid_json=True))
        with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(RuntimeError, match="Invalid JSON"):
            await c.fetch_data(source)

    async def test_fetch_network_error_propagates(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.ConnectError("connection failed")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.ConnectError):
            await c.fetch_data(source)

    async def test_fetch_timeout_propagates(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.TimeoutException("timed out")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.TimeoutException):
            await c.fetch_data(source)

    async def test_fetch_no_subreddits_returns_none(self):
        c = RedditCollector()
        assert await c.fetch_data(_make_source(config={})) is None
        assert await c.fetch_data(_make_source(config={"subreddits": []})) is None

    async def test_fetch_skips_malformed_children(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        payload = {"data": {"children": [None, {"kind": "t3"}, {"kind": "t3", "data": "oops"}, {"data": _make_post()}]}}
        mock_client = _mock_http_client(_mock_response(200, payload))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 1
        assert result[0]["title"] == "Test post"

    async def test_fetch_missing_data_section_yields_empty(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        mock_client = _mock_http_client(_mock_response(200, {}))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []


# ── parse_data ────────────────────────────────────────────────────
class TestRedditParse:
    async def test_parse_link_post_full_mapping(self):
        c = RedditCollector()
        post = _make_post(selftext="A short selftext")
        result = await c.parse_data([post], _make_source())

        assert len(result) == 1
        item = result[0]
        assert item["title"] == "Test post"
        # external link post keeps the external url
        assert item["url"] == "https://example.com/article"
        assert item["summary"] == "A short selftext"
        assert item["published_at"] == "2023-11-14T22:13:20+00:00"
        assert item["author"] == "tester"
        assert item["extra_data"] == {
            "reddit_score": 42,
            "num_comments": 7,
            "subreddit": "artificial",
            "author": "tester",
        }

    async def test_parse_self_post_url_falls_back_to_permalink(self):
        c = RedditCollector()
        post = _make_post(is_self=True, url="https://www.reddit.com/r/space/comments/x/", selftext="")
        result = await c.parse_data([post], _make_source())

        assert result[0]["url"] == "https://www.reddit.com/r/artificial/comments/abc123/test_post/"
        assert result[0]["summary"] == "Submitted by tester"

    async def test_parse_no_url_falls_back_to_permalink(self):
        c = RedditCollector()
        post = _make_post(url="", selftext="")
        result = await c.parse_data([post], _make_source())
        assert result[0]["url"] == "https://www.reddit.com/r/artificial/comments/abc123/test_post/"

    async def test_parse_selftext_truncated_to_300_chars(self):
        c = RedditCollector()
        post = _make_post(selftext="x" * 500)
        result = await c.parse_data([post], _make_source())
        assert result[0]["summary"] == "x" * 300

    async def test_parse_no_selftext_summary_uses_author(self):
        c = RedditCollector()
        post = _make_post(selftext="", author="someone")
        result = await c.parse_data([post], _make_source())
        assert result[0]["summary"] == "Submitted by someone"

    async def test_parse_missing_author_summary_fallback(self):
        c = RedditCollector()
        post = _make_post(selftext="", author=None)
        result = await c.parse_data([post], _make_source())
        assert result[0]["summary"] == "Submitted by unknown"
        assert result[0]["author"] == ""
        assert result[0]["extra_data"]["author"] == ""

    async def test_parse_missing_created_utc_empty_published_at(self):
        c = RedditCollector()
        post = _make_post(created_utc=None)
        result = await c.parse_data([post], _make_source())
        assert result[0]["published_at"] == ""

    async def test_parse_invalid_created_utc_empty_published_at(self):
        c = RedditCollector()
        post = _make_post(created_utc="not-a-timestamp")
        result = await c.parse_data([post], _make_source())
        assert result[0]["published_at"] == ""

    async def test_parse_missing_extra_fields_default(self):
        c = RedditCollector()
        post = _make_post()
        for key in ("score", "num_comments", "subreddit"):
            post.pop(key)
        result = await c.parse_data([post], _make_source())
        assert result[0]["extra_data"]["reddit_score"] == 0
        assert result[0]["extra_data"]["num_comments"] == 0
        assert result[0]["extra_data"]["subreddit"] == ""

    async def test_parse_skips_posts_without_title(self):
        c = RedditCollector()
        posts = [_make_post(title=""), _make_post(title="   "), _make_post()]
        result = await c.parse_data(posts, _make_source())
        assert len(result) == 1

    async def test_parse_empty_and_invalid_input(self):
        c = RedditCollector()
        source = _make_source()
        assert await c.parse_data(None, source) == []
        assert await c.parse_data([], source) == []
        assert await c.parse_data("not a list", source) == []
        assert await c.parse_data(["not a dict"], source) == []


# ── collect() end-to-end ──────────────────────────────────────────
class TestRedditCollect:
    async def test_collect_success(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        payload = _listing_payload([_make_post()])
        mock_client = _mock_http_client(_mock_response(200, payload))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is True
        assert len(result.items) == 1
        assert result.items[0]["title"] == "Test post"

    async def test_collect_429_is_empty_success(self):
        c = RedditCollector()
        source = _make_source(config={"subreddits": ["space"]})
        mock_client = _mock_http_client(_mock_response(429, {}))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is True
        assert result.items == []

    async def test_collect_network_failure_marks_unsuccessful(self):
        c = RedditCollector()
        c.retry_base_delay_seconds = 0.01
        source = _make_source(config={"subreddits": ["space"]})
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.ConnectError("connection failed")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch("httpx.AsyncClient", return_value=client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is False
        assert "retry" in result.error.lower()
