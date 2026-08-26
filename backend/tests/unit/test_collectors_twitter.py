"""Unit tests for app/collectors/tech/twitter_collector.py."""

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.collectors import COLLECTOR_REGISTRY, get_collector, resolve_collector
from app.collectors.tech.twitter_collector import TwitterCollector, _parse_created_at, _query_list


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


def _make_tweet(**kwargs):
    tweet = {
        "id": "1234567890",
        "text": "Robotics breakthrough announced today",
        "created_at": "2026-08-25T10:00:00.000Z",
        "public_metrics": {
            "retweet_count": 12,
            "reply_count": 3,
            "like_count": 87,
            "quote_count": 2,
            "bookmark_count": 1,
            "impression_count": 15000,
        },
    }
    tweet.update(kwargs)
    return tweet


def _search_payload(tweets):
    return {"data": tweets, "meta": {"result_count": len(tweets)}}


@contextmanager
def _patch_settings(token="test-token"):
    with patch("app.collectors.tech.twitter_collector.settings") as mock_settings:
        mock_settings.twitter_bearer_token = token
        yield mock_settings


# ── Registry / resolve_collector ─────────────────────────────────
class TestTwitterRegistry:
    def test_registered_in_collector_registry(self):
        assert "twitter" in COLLECTOR_REGISTRY
        assert get_collector("twitter") is TwitterCollector

    def test_resolve_collector_social_with_twitter_library(self):
        assert resolve_collector("social", {"library": "twitter"}) is TwitterCollector
        assert resolve_collector("social", {"library": "twitter", "query": "AI OR robotics"}) is TwitterCollector

    def test_resolve_collector_social_without_library(self):
        assert resolve_collector("social", None) is None
        assert resolve_collector("social", {}) is None
        assert resolve_collector("social", {"platform": "twitter"}) is None

    def test_resolve_collector_social_unknown_library(self):
        assert resolve_collector("social", {"library": "nonexistent"}) is None


# ── config.query normalization ───────────────────────────────────
class TestQueryList:
    def test_list_input(self):
        assert _query_list(["AI", "robotics"]) == ["AI", "robotics"]

    def test_string_input_comma_separated_preserves_operators(self):
        assert _query_list("AI OR robotics, lang:en space") == ["AI OR robotics", "lang:en space"]

    def test_strips_whitespace_and_dedupes_preserving_order(self):
        assert _query_list([" AI ", "AI", "robotics"]) == ["AI", "robotics"]
        assert _query_list("AI, AI ,robotics") == ["AI", "robotics"]

    def test_empty_and_invalid_input(self):
        assert _query_list(None) == []
        assert _query_list("") == []
        assert _query_list(" , ,") == []
        assert _query_list(12345) == []


# ── created_at parsing ───────────────────────────────────────────
class TestParseCreatedAt:
    def test_valid_iso_with_z_suffix(self):
        assert _parse_created_at("2026-08-25T10:00:00.000Z") == "2026-08-25T10:00:00+00:00"

    def test_valid_iso_without_z(self):
        assert _parse_created_at("2026-08-25T10:00:00+02:00") == "2026-08-25T08:00:00+00:00"

    def test_naive_datetime_assumed_utc(self):
        assert _parse_created_at("2026-08-25T10:00:00") == "2026-08-25T10:00:00+00:00"

    def test_invalid_and_missing(self):
        assert _parse_created_at(None) == ""
        assert _parse_created_at("") == ""
        assert _parse_created_at("not-a-date") == ""
        assert _parse_created_at(12345) == ""


# ── fetch_data ────────────────────────────────────────────────────
class TestTwitterFetch:
    async def test_fetch_single_query_success(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI OR robotics"})
        payload = _search_payload([_make_tweet()])
        mock_client = _mock_http_client(_mock_response(200, payload))
        with _patch_settings("test-token"), patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["id"] == "1234567890"
        url, headers, params = mock_client.calls[0]
        assert url == "https://api.twitter.com/2/tweets/search/recent"
        assert headers["Authorization"] == "Bearer test-token"
        assert params == {
            "query": "AI OR robotics",
            "max_results": TwitterCollector.DEFAULT_MAX_RESULTS,
            "tweet.fields": "created_at,public_metrics",
        }

    async def test_fetch_no_token_returns_none(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        with _patch_settings(None):
            assert await c.fetch_data(source) is None
        with _patch_settings(""):
            assert await c.fetch_data(source) is None

    async def test_fetch_no_query_returns_none(self):
        c = TwitterCollector()
        with _patch_settings():
            assert await c.fetch_data(_make_source(config={})) is None
            assert await c.fetch_data(_make_source(config={"query": ""})) is None
            assert await c.fetch_data(_make_source(config={"query": []})) is None

    async def test_fetch_multiple_queries_merges_and_dedupes(self):
        c = TwitterCollector()
        source = _make_source(config={"query": ["AI", "robotics"]})
        shared = _make_tweet(id="shared-1", text="shared tweet")
        first = _search_payload([_make_tweet(id="one"), shared])
        second = _search_payload([shared, _make_tweet(id="two")])
        mock_client = _mock_http_client(_mock_response(200, first), _mock_response(200, second))
        with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert [t["id"] for t in result] == ["one", "shared-1", "two"]
        assert mock_client.calls[0][2]["query"] == "AI"
        assert mock_client.calls[1][2]["query"] == "robotics"

    async def test_fetch_max_results_clamped_and_invalid_falls_back(self):
        c = TwitterCollector()
        for raw, expected in ((500, 100), (5, 10), ("not-a-number", TwitterCollector.DEFAULT_MAX_RESULTS)):
            source = _make_source(config={"query": "AI", "max_results": raw})
            mock_client = _mock_http_client(_mock_response(200, _search_payload([])))
            with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
                await c.fetch_data(source)
            assert mock_client.calls[0][2] == {
                "query": "AI",
                "max_results": expected,
                "tweet.fields": "created_at,public_metrics",
            }

    async def test_fetch_429_returns_empty_and_stops(self):
        c = TwitterCollector()
        source = _make_source(config={"query": ["AI", "robotics"]})
        mock_client = _mock_http_client(_mock_response(429, {}), _mock_response(200, _search_payload([])))
        with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result == []
        # second query must not be fetched once rate limited
        assert len(mock_client.calls) == 1

    async def test_fetch_subsequent_query_429_returns_empty(self):
        c = TwitterCollector()
        source = _make_source(config={"query": ["AI", "robotics"]})
        ok = _mock_response(200, _search_payload([_make_tweet()]))
        mock_client = _mock_http_client(ok, _mock_response(429, {}))
        with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)

        assert result == []
        assert len(mock_client.calls) == 2

    async def test_fetch_401_and_403_return_empty(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        for status in (401, 403):
            mock_client = _mock_http_client(_mock_response(status, {}))
            with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
                result = await c.fetch_data(source)
            assert result == []

    async def test_fetch_non_200_raises(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        mock_client = _mock_http_client(_mock_response(500, {}))
        with (
            _patch_settings(),
            patch("httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RuntimeError, match="HTTP 500"),
        ):
            await c.fetch_data(source)

    async def test_fetch_invalid_json_raises(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        mock_client = _mock_http_client(_mock_response(200, invalid_json=True))
        with (
            _patch_settings(),
            patch("httpx.AsyncClient", return_value=mock_client),
            pytest.raises(RuntimeError, match="Invalid JSON"),
        ):
            await c.fetch_data(source)

    async def test_fetch_network_error_propagates(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.ConnectError("connection failed")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with _patch_settings(), patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.ConnectError):
            await c.fetch_data(source)

    async def test_fetch_timeout_propagates(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.TimeoutException("timed out")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with _patch_settings(), patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.TimeoutException):
            await c.fetch_data(source)

    async def test_fetch_skips_malformed_data_entries(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        payload = {"data": [None, "oops", _make_tweet()]}
        mock_client = _mock_http_client(_mock_response(200, payload))
        with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert len(result) == 1
        assert result[0]["id"] == "1234567890"

    async def test_fetch_missing_data_section_yields_empty(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        mock_client = _mock_http_client(_mock_response(200, {}))
        with _patch_settings(), patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(source)
        assert result == []


# ── parse_data ────────────────────────────────────────────────────
class TestTwitterParse:
    async def test_parse_full_mapping(self):
        c = TwitterCollector()
        result = await c.parse_data([_make_tweet()], _make_source())

        assert len(result) == 1
        item = result[0]
        assert item["title"] == "Robotics breakthrough announced today"
        assert item["url"] == "https://twitter.com/i/status/1234567890"
        assert item["summary"] == "Robotics breakthrough announced today"
        assert item["published_at"] == "2026-08-25T10:00:00+00:00"
        assert item["extra_data"] == {
            "like_count": 87,
            "retweet_count": 12,
            "reply_count": 3,
            "impression_count": 15000,
        }

    async def test_parse_title_truncated_to_200_chars_summary_kept(self):
        c = TwitterCollector()
        tweet = _make_tweet(text="x" * 250)
        result = await c.parse_data([tweet], _make_source())
        assert result[0]["title"] == "x" * 200
        assert result[0]["summary"] == "x" * 250

    async def test_parse_partial_metrics_only_present_keys(self):
        c = TwitterCollector()
        tweet = _make_tweet(public_metrics={"like_count": 5})
        result = await c.parse_data([tweet], _make_source())
        assert result[0]["extra_data"] == {"like_count": 5}

    async def test_parse_missing_or_malformed_metrics_yield_empty_extra(self):
        c = TwitterCollector()
        no_metrics = _make_tweet()
        no_metrics.pop("public_metrics")
        malformed = _make_tweet(id="other", public_metrics="oops")
        result = await c.parse_data([no_metrics, malformed], _make_source())
        assert result[0]["extra_data"] == {}
        assert result[1]["extra_data"] == {}

    async def test_parse_skips_empty_text_and_missing_id(self):
        c = TwitterCollector()
        tweets = [_make_tweet(text=""), _make_tweet(text="   "), _make_tweet(id=""), _make_tweet()]
        result = await c.parse_data(tweets, _make_source())
        assert len(result) == 1

    async def test_parse_missing_created_at_empty_published_at(self):
        c = TwitterCollector()
        tweet = _make_tweet(created_at=None)
        result = await c.parse_data([tweet], _make_source())
        assert result[0]["published_at"] == ""

    async def test_parse_empty_and_invalid_input(self):
        c = TwitterCollector()
        source = _make_source()
        assert await c.parse_data(None, source) == []
        assert await c.parse_data([], source) == []
        assert await c.parse_data("not a list", source) == []
        assert await c.parse_data(["not a dict"], source) == []


# ── collect() end-to-end ──────────────────────────────────────────
class TestTwitterCollect:
    async def test_collect_success(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        payload = _search_payload([_make_tweet()])
        mock_client = _mock_http_client(_mock_response(200, payload))
        with (
            _patch_settings(),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is True
        assert len(result.items) == 1
        assert result.items[0]["title"] == "Robotics breakthrough announced today"

    async def test_collect_429_is_empty_success(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        mock_client = _mock_http_client(_mock_response(429, {}))
        with (
            _patch_settings(),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is True
        assert result.items == []

    async def test_collect_no_token_marks_unsuccessful(self):
        c = TwitterCollector()
        source = _make_source(config={"query": "AI"})
        with (
            _patch_settings(None),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is False
        assert "retry" in result.error.lower()
