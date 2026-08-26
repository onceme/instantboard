"""Unit tests for app/collectors/tech/web_scrape_collector.py."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from bs4 import BeautifulSoup as RealBeautifulSoup
from bs4 import FeatureNotFound

from app.collectors import COLLECTOR_REGISTRY, get_collector, resolve_collector
from app.collectors.finance.eastmoney_collector import EastMoneyCollector
from app.collectors.tech.web_scrape_collector import WebScrapeCollector

SAMPLE_HTML = """
<html>
<body>
  <div class="list">
    <article>
      <h2><a href="/blog/first-post">First Post</a></h2>
      <p class="excerpt">Summary of the first post.</p>
      <time datetime="2026-08-01T10:00:00Z">August 1, 2026</time>
    </article>
    <article>
      <h2>No Link Post</h2>
      <p class="excerpt">This item has no anchor at all.</p>
    </article>
    <article>
      <h2><a href="https://other.example/abs">Absolute Link</a></h2>
      <p class="excerpt">Absolute summary.</p>
      <span class="date">not a real date</span>
    </article>
    <article>
      <p class="excerpt">This item has no title.</p>
      <a href="/blog/no-title">a link without title</a>
    </article>
  </div>
</body>
</html>
"""

FULL_RULES = {
    "item_selector": "article",
    "title_selector": "h2",
    "link_selector": "a",
    "summary_selector": "p.excerpt",
    "date_selector": "time",
}


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "source-id")
    src.name = kwargs.get("name", "Test Source")
    src.url = kwargs.get("url", "https://example.com/blog")
    src.config = kwargs.get("config", {"parse_rules": dict(FULL_RULES)})
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    src.category = kwargs.get("category", MagicMock())
    return src


def _mock_response(status_code: int = 200, text: str = ""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    return resp


def _mock_http_client(*responses):
    """Mock httpx.AsyncClient. Repeats a single response; pops through a sequence.

    Requests are recorded in ``client.calls`` as (url, headers) tuples.
    """
    client = AsyncMock()
    seq = list(responses)
    calls: list[tuple[str, dict | None]] = []

    async def mock_get(url, headers=None, params=None):
        calls.append((url, headers))
        if len(seq) > 1:
            return seq.pop(0)
        return seq[0]

    client.get = mock_get
    client.calls = calls
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


def _articles_html(count: int) -> str:
    items = "".join(f'<article><h2><a href="/p/{i}">Post {i}</a></h2></article>' for i in range(count))
    return f"<html><body>{items}</body></html>"


# ── Registry / resolve_collector ─────────────────────────────────
class TestWebScrapeRegistry:
    def test_registered_in_collector_registry(self):
        assert len(COLLECTOR_REGISTRY) == 11
        assert "web_scrape" in COLLECTOR_REGISTRY
        assert get_collector("web_scrape") is WebScrapeCollector

    def test_resolve_collector_web_scrape_by_source_type(self):
        assert resolve_collector("web_scrape", None) is WebScrapeCollector
        assert resolve_collector("web_scrape", {}) is WebScrapeCollector
        assert resolve_collector("web_scrape", {"parse_rules": dict(FULL_RULES)}) is WebScrapeCollector

    def test_resolve_collector_explicit_library_overrides_source_type(self):
        """The eastmoney seed (source_type=web_scrape + library=eastmoney) must keep
        resolving to EastMoneyCollector — the explicit library wins over the generic
        scraper registered for source_type=web_scrape."""
        assert resolve_collector("web_scrape", {"library": "eastmoney"}) is EastMoneyCollector
        assert (
            resolve_collector("web_scrape", {"library": "eastmoney", "data_type": "cn_indices"}) is EastMoneyCollector
        )

    def test_resolve_collector_unknown_library_falls_back_to_source_type(self):
        assert resolve_collector("web_scrape", {"library": "nonexistent"}) is WebScrapeCollector

    def test_resolve_collector_bare_api_and_social_still_unresolvable(self):
        assert resolve_collector("api", None) is None
        assert resolve_collector("api", {}) is None
        assert resolve_collector("social", None) is None
        assert resolve_collector("social", {"platform": "twitter", "query": "x"}) is None
        assert resolve_collector("social", {"library": "nonexistent"}) is None


# ── Seed wiring regression ────────────────────────────────────────
class TestWebScrapeSeeds:
    def test_tech_web_scrape_seeds_active_with_parse_rules(self):
        from app.db.init_db import (
            TECH_AI_SOURCES,
            TECH_EMBEDDED_SOURCES,
            TECH_ROBOTICS_SOURCES,
            TECH_SPACE_SOURCES,
        )

        tech_sources = TECH_AI_SOURCES + TECH_ROBOTICS_SOURCES + TECH_EMBEDDED_SOURCES + TECH_SPACE_SOURCES
        scrape_seeds = [src for src in tech_sources if src["source_type"] == "web_scrape"]
        assert {src["name"] for src in scrape_seeds} == {
            "OpenAI Blog",
            "Automotive News",
            "RISC-V International Blog",
            "SpaceX Updates",
        }
        for src in scrape_seeds:
            assert src.get("is_active") is True, src["name"]
            rules = src["config"]["parse_rules"]
            assert rules.get("item_selector"), src["name"]
            assert rules.get("title_selector"), src["name"]
            assert resolve_collector(src["source_type"], src["config"]) is WebScrapeCollector

    def test_eastmoney_seed_still_resolves_to_eastmoney_collector(self):
        from app.db.init_db import FINANCE_SOURCES

        eastmoney = next(src for src in FINANCE_SOURCES if src["name"] == "东方财富-A股实时")
        assert eastmoney.get("is_active", True) is True
        assert resolve_collector(eastmoney["source_type"], eastmoney["config"]) is EastMoneyCollector

    def test_tiantian_fund_seed_stays_template(self):
        from app.db.init_db import FINANCE_SOURCES

        fund = next(src for src in FINANCE_SOURCES if src["name"] == "天天基金-官方NAV")
        # Collector is resolvable now, but the NAV consumption chain is a follow-up
        # feature, so the seed remains an inactive template.
        assert fund["is_active"] is False
        assert resolve_collector(fund["source_type"], fund["config"]) is WebScrapeCollector


# ── fetch_data ────────────────────────────────────────────────────
class TestWebScrapeFetch:
    async def test_fetch_success_returns_html(self):
        c = WebScrapeCollector()
        source = _make_source()
        mock_client = _mock_http_client(_mock_response(200, SAMPLE_HTML))
        with patch("httpx.AsyncClient", return_value=mock_client) as client_cls:
            result = await c.fetch_data(source)

        assert result == SAMPLE_HTML
        client_cls.assert_called_once_with(timeout=c.timeout_seconds, follow_redirects=True)
        url, headers = mock_client.calls[0]
        assert url == "https://example.com/blog"
        assert headers["User-Agent"] == WebScrapeCollector.DEFAULT_USER_AGENT

    async def test_fetch_custom_user_agent(self):
        c = WebScrapeCollector()
        source = _make_source(config={"user_agent": "my-bot/2.0", "parse_rules": dict(FULL_RULES)})
        mock_client = _mock_http_client(_mock_response(200, "<html></html>"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            await c.fetch_data(source)
        assert mock_client.calls[0][1]["User-Agent"] == "my-bot/2.0"

    async def test_fetch_429_returns_empty(self):
        c = WebScrapeCollector()
        mock_client = _mock_http_client(_mock_response(429, "slow down"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(_make_source())
        assert result == []

    async def test_fetch_403_returns_empty(self):
        c = WebScrapeCollector()
        mock_client = _mock_http_client(_mock_response(403, "forbidden"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await c.fetch_data(_make_source())
        assert result == []

    async def test_fetch_non_200_raises(self):
        c = WebScrapeCollector()
        mock_client = _mock_http_client(_mock_response(500, "boom"))
        with patch("httpx.AsyncClient", return_value=mock_client), pytest.raises(RuntimeError, match="HTTP 500"):
            await c.fetch_data(_make_source())

    async def test_fetch_timeout_propagates(self):
        c = WebScrapeCollector()
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.TimeoutException("timed out")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.TimeoutException):
            await c.fetch_data(_make_source())

    async def test_fetch_network_error_propagates(self):
        c = WebScrapeCollector()
        client = AsyncMock()

        async def mock_get(url, headers=None, params=None):
            raise httpx.ConnectError("connection failed")

        client.get = mock_get
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client), pytest.raises(httpx.ConnectError):
            await c.fetch_data(_make_source())

    async def test_fetch_no_url_returns_none(self):
        c = WebScrapeCollector()
        assert await c.fetch_data(_make_source(url="")) is None


# ── parse_data ────────────────────────────────────────────────────
class TestWebScrapeParse:
    async def test_parse_full_mapping_and_skips(self):
        c = WebScrapeCollector()
        source = _make_source()
        result = await c.parse_data(SAMPLE_HTML, source)

        # "No Link Post" (no anchor) and the title-less item are skipped.
        assert len(result) == 2

        first = result[0]
        assert first["title"] == "First Post"
        # relative href is joined against source.url
        assert first["url"] == "https://example.com/blog/first-post"
        assert first["summary"] == "Summary of the first post."
        assert first["published_at"] == "2026-08-01T10:00:00+00:00"
        assert first["extra_data"]["scraped_url"] == "https://example.com/blog"
        assert first["extra_data"]["scraped_at"]

        second = result[1]
        assert second["title"] == "Absolute Link"
        assert second["url"] == "https://other.example/abs"
        assert second["summary"] == "Absolute summary."

    async def test_parse_unparseable_date_falls_back_to_now(self):
        c = WebScrapeCollector()
        rules = dict(FULL_RULES)
        rules["date_selector"] = ".date"  # matches <span class="date">not a real date</span>
        result = await c.parse_data(SAMPLE_HTML, _make_source(config={"parse_rules": rules}))

        assert len(result) == 2
        parsed = datetime.fromisoformat(result[1]["published_at"])
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 60

    async def test_parse_time_datetime_attribute_preferred_over_text(self):
        c = WebScrapeCollector()
        html = '<html><body><article><h2><a href="/a">A</a></h2><time datetime="2026-01-02T03:04:05+00:00">Jan 2</time></article></body></html>'
        rules = {"item_selector": "article", "title_selector": "h2", "date_selector": "time"}
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        assert result[0]["published_at"] == "2026-01-02T03:04:05+00:00"

    async def test_parse_no_date_selector_uses_now(self):
        c = WebScrapeCollector()
        html = '<html><body><article><h2><a href="/a">A</a></h2></article></body></html>'
        rules = {"item_selector": "article", "title_selector": "h2"}
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        parsed = datetime.fromisoformat(result[0]["published_at"])
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 60

    async def test_parse_skips_items_without_title(self):
        c = WebScrapeCollector()
        html = '<html><body><article><a href="/a">link only</a></article></body></html>'
        result = await c.parse_data(html, _make_source())
        assert result == []

    async def test_parse_skips_items_without_url(self):
        c = WebScrapeCollector()
        html = "<html><body><article><h2>Title without link</h2></article></body></html>"
        result = await c.parse_data(html, _make_source())
        assert result == []

    async def test_parse_skips_ignored_link_schemes(self):
        c = WebScrapeCollector()
        html = (
            "<html><body>"
            '<article><h2><a href="javascript:void(0)">JS</a></h2></article>'
            '<article><h2><a href="#">Hash</a></h2></article>'
            '<article><h2><a href="mailto:x@example.com">Mail</a></h2></article>'
            "</body></html>"
        )
        result = await c.parse_data(html, _make_source())
        assert result == []

    async def test_parse_title_falls_back_to_heading(self):
        c = WebScrapeCollector()
        html = '<html><body><article><h3><a href="/h">Heading Three</a></h3></article></body></html>'
        rules = {"item_selector": "article", "title_selector": "h2"}  # misses; h3 fallback
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        assert result[0]["title"] == "Heading Three"

    async def test_parse_without_link_selector_uses_first_anchor(self):
        c = WebScrapeCollector()
        html = (
            '<html><body><article><h2>T</h2><a href="/first">one</a><a href="/second">two</a></article></body></html>'
        )
        rules = {"item_selector": "article", "title_selector": "h2"}
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        assert result[0]["url"] == "https://example.com/first"

    async def test_parse_without_item_selector_uses_title_elements(self):
        c = WebScrapeCollector()
        html = (
            "<html><body>"
            '<h2><a href="/one">One</a></h2>'
            "<h2>No link heading</h2>"
            '<h2><a href="/two">Two</a></h2>'
            "</body></html>"
        )
        rules = {"title_selector": "h2"}
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        assert [item["title"] for item in result] == ["One", "Two"]
        assert result[0]["url"] == "https://example.com/one"

    async def test_parse_title_element_as_anchor_uses_own_href(self):
        c = WebScrapeCollector()
        html = '<html><body><a class="titlelink" href="/post/1">Hello</a></body></html>'
        rules = {"title_selector": "a.titlelink"}
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        assert result[0]["title"] == "Hello"
        assert result[0]["url"] == "https://example.com/post/1"

    async def test_parse_summary_truncated(self):
        c = WebScrapeCollector()
        html = f'<html><body><article><h2><a href="/a">A</a></h2><p class="excerpt">{"x" * 500}</p></article></body></html>'
        result = await c.parse_data(html, _make_source())
        assert result[0]["summary"] == "x" * WebScrapeCollector.SUMMARY_MAX_CHARS

    async def test_parse_no_summary_selector_empty_summary(self):
        c = WebScrapeCollector()
        html = '<html><body><article><h2><a href="/a">A</a></h2><p>text</p></article></body></html>'
        rules = {"item_selector": "article", "title_selector": "h2"}
        result = await c.parse_data(html, _make_source(config={"parse_rules": rules}))
        assert result[0]["summary"] == ""

    async def test_parse_limit_truncates(self):
        c = WebScrapeCollector()
        rules = dict(FULL_RULES)
        rules["limit"] = 3
        result = await c.parse_data(_articles_html(5), _make_source(config={"parse_rules": rules}))
        assert len(result) == 3
        assert [item["title"] for item in result] == ["Post 0", "Post 1", "Post 2"]

    async def test_parse_limit_default_is_20(self):
        c = WebScrapeCollector()
        result = await c.parse_data(_articles_html(25), _make_source())
        assert len(result) == WebScrapeCollector.DEFAULT_LIMIT

    async def test_parse_limit_capped_at_max(self):
        c = WebScrapeCollector()
        rules = dict(FULL_RULES)
        rules["limit"] = 5000
        result = await c.parse_data(_articles_html(120), _make_source(config={"parse_rules": rules}))
        assert len(result) == WebScrapeCollector.MAX_LIMIT

    async def test_parse_invalid_limit_falls_back_to_default(self):
        c = WebScrapeCollector()
        rules = dict(FULL_RULES)
        rules["limit"] = "not-a-number"
        result = await c.parse_data(_articles_html(25), _make_source(config={"parse_rules": rules}))
        assert len(result) == WebScrapeCollector.DEFAULT_LIMIT

    async def test_parse_lxml_failure_falls_back_to_html_parser(self):
        c = WebScrapeCollector()
        features_used: list[str] = []

        def fake_beautiful_soup(markup, features):
            features_used.append(features)
            if features == "lxml":
                raise FeatureNotFound("lxml not installed")
            return RealBeautifulSoup(markup, features)

        with patch("app.collectors.tech.web_scrape_collector.BeautifulSoup", side_effect=fake_beautiful_soup):
            result = await c.parse_data(SAMPLE_HTML, _make_source())

        assert features_used == ["lxml", "html.parser"]
        assert len(result) == 2
        assert result[0]["title"] == "First Post"

    async def test_parse_empty_and_invalid_input(self):
        c = WebScrapeCollector()
        source = _make_source()
        assert await c.parse_data(None, source) == []
        assert await c.parse_data("", source) == []
        assert await c.parse_data(12345, source) == []
        assert await c.parse_data(["<html></html>"], source) == []

    async def test_parse_no_selectors_returns_empty(self):
        c = WebScrapeCollector()
        assert await c.parse_data(SAMPLE_HTML, _make_source(config={})) == []
        assert await c.parse_data(SAMPLE_HTML, _make_source(config={"parse_rules": {}})) == []

    async def test_parse_non_dict_rules_returns_empty(self):
        c = WebScrapeCollector()
        source = _make_source(config={"parse_rules": "garbage"})
        assert await c.parse_data(SAMPLE_HTML, source) == []


# ── collect() end-to-end ──────────────────────────────────────────
class TestWebScrapeCollect:
    async def test_collect_success(self):
        c = WebScrapeCollector()
        source = _make_source()
        mock_client = _mock_http_client(_mock_response(200, SAMPLE_HTML))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is True
        assert len(result.items) == 2
        assert result.items[0]["title"] == "First Post"

    async def test_collect_429_is_empty_success(self):
        c = WebScrapeCollector()
        source = _make_source()
        mock_client = _mock_http_client(_mock_response(429, ""))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is True
        assert result.items == []

    async def test_collect_http_500_marks_unsuccessful(self):
        c = WebScrapeCollector()
        c.retry_base_delay_seconds = 0.01
        source = _make_source()
        mock_client = _mock_http_client(_mock_response(500, "boom"))
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch("app.collectors.base.redis_get", new_callable=AsyncMock, return_value=None),
            patch("app.collectors.base.redis_set", new_callable=AsyncMock),
        ):
            result = await c.collect(source)
        assert result.success is False
        assert "retry" in result.error.lower()

    async def test_collect_network_failure_marks_unsuccessful(self):
        c = WebScrapeCollector()
        c.retry_base_delay_seconds = 0.01
        source = _make_source()
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
