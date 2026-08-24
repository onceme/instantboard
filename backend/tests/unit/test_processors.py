"""Unit tests for app/processors package."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processors import (
    BaseProcessor,
    CategorizerProcessor,
    DedupProcessor,
    FilterProcessor,
    ProcessorChain,
    ProcessResult,
    TransformerProcessor,
    create_default_processor_chain,
)
from app.processors.categorizer import KEYWORD_TO_TAG, TechTopicExtractor
from app.processors.filter import BLACKLIST_KEYWORDS, MIN_TITLE_LENGTH


def _make_source(**kwargs):
    src = MagicMock()
    src.id = kwargs.get("id", "src-id")
    src.name = kwargs.get("name", "Test Source")
    src.tenant_id = kwargs.get("tenant_id", "test-tenant")
    category = kwargs.get("category")
    if category is None:
        cat = MagicMock()
        cat.id = "cat-id"
        cat.type = "tech"
        cat.slug = "tech"
        cat.keywords_filter = []
        src.category = cat
    else:
        src.category = category
    src.keywords_filter = kwargs.get("keywords_filter", [])
    src.priority = kwargs.get("priority", 5)
    return src


# ── BaseProcessor / ProcessResult / ProcessorChain ──────────────
class TestBaseProcessor:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseProcessor()


class TestProcessResult:
    def test_defaults(self):
        r = ProcessResult(item={"a": 1})
        assert r.item == {"a": 1}
        assert r.metadata == {}
        assert r.errors == []


class TestProcessorChain:
    async def test_execute_single_processor(self):
        class IdentityProc(BaseProcessor):
            async def process(self, item, source):
                return item

        chain = ProcessorChain([IdentityProc()])
        results = await chain.execute([{"a": 1}], _make_source())
        assert len(results) == 1
        assert results[0].item == {"a": 1}

    async def test_execute_filters_none(self):
        class DropProc(BaseProcessor):
            async def process(self, item, source):
                return None

        chain = ProcessorChain([DropProc()])
        results = await chain.execute([{"a": 1}], _make_source())
        assert results[0].item is None
        assert results[0].metadata["filtered_by"] == "DropProc"

    async def test_execute_processor_error(self):
        class BadProc(BaseProcessor):
            async def process(self, item, source):
                raise ValueError("oops")

        chain = ProcessorChain([BadProc()])
        results = await chain.execute([{"url": "http://x.com", "a": 1}], _make_source())
        assert len(results) == 1
        assert len(results[0].errors) == 1
        assert "BadProc" in results[0].errors[0]

    async def test_add(self):
        class P1(BaseProcessor):
            async def process(self, item, source):
                return item

        chain = ProcessorChain()
        chain.add(P1())
        assert len(chain.processors) == 1

    async def test_empty_items(self):
        chain = ProcessorChain([])
        results = await chain.execute([], _make_source())
        assert results == []


# ── create_default_processor_chain ───────────────────────────────
class TestCreateDefaultChain:
    def test_returns_proper_chain(self):
        chain = create_default_processor_chain()
        assert isinstance(chain, ProcessorChain)
        assert len(chain.processors) == 4
        types = [type(p) for p in chain.processors]
        assert types[0] is DedupProcessor
        assert types[1] is FilterProcessor
        assert types[2] is CategorizerProcessor
        assert types[3] is TransformerProcessor


# ── TechTopicExtractor ───────────────────────────────────────────
class TestTechTopicExtractor:
    def test_extract_tags_ai(self):
        ext = TechTopicExtractor()
        tags = ext.extract_tags("New GPT model released")
        assert "ai" in tags

    def test_extract_tags_robotics(self):
        ext = TechTopicExtractor()
        tags = ext.extract_tags("Humanoid robot by Boston Dynamics")
        assert "robotics" in tags

    def test_extract_tags_embedded(self):
        ext = TechTopicExtractor()
        tags = ext.extract_tags("New RISC-V chip announced")
        assert "embedded" in tags
        assert "risc-v" in tags

    def test_extract_tags_no_match_returns_general(self):
        ext = TechTopicExtractor()
        tags = ext.extract_tags("Random topic not in dictionary")
        assert "general" in tags

    def test_extract_with_level1(self):
        ext = TechTopicExtractor()
        tags = ext.extract_with_level1("GPT model", "ai")
        assert tags[0] == "ai"

    def test_extract_with_level1_adds_level1(self):
        ext = TechTopicExtractor()
        tags = ext.extract_with_level1("something general", "tech")
        assert tags[0] == "tech"

    def test_sort_tags_ordering(self):
        ext = TechTopicExtractor()
        tags_set = {"llm", "ai", "china-stock"}
        result = ext._sort_tags(tags_set)
        assert result[0] == "ai"
        assert "china-stock" in result
        assert "llm" in result


# ── CategorizerProcessor ────────────────────────────────────────
class TestCategorizerProcessor:
    async def test_process_tech_category(self):
        c = CategorizerProcessor()
        source = _make_source()
        item = {"title": "New LLM model announced", "summary": "GPT breakthrough"}
        result = await c.process(item, source)
        assert result is not None
        assert "tech" in result["topic_tags"]

    async def test_process_finance_category(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "cat-fin"
        cat.type = "finance"
        cat.slug = "finance"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "A股行情", "summary": "上证指数上涨", "type": "cn_index"}
        result = await c.process(item, source)
        assert result is not None
        assert "finance" in result["topic_tags"]

    async def test_process_no_category(self):
        c = CategorizerProcessor()
        source = _make_source(category=None)
        item = {"title": "Some AI news", "summary": "About LLM"}
        result = await c.process(item, source)
        assert result is not None
        assert isinstance(result["topic_tags"], list)

    async def test_process_finance_type_cn_stock(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "f"
        cat.type = "finance"
        cat.slug = "finance"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "Stock", "summary": "", "type": "cn_stock"}
        result = await c.process(item, source)
        assert "china-stock" in result["topic_tags"]

    async def test_process_finance_type_commodity(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "f"
        cat.type = "finance"
        cat.slug = "finance"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "Gold price", "summary": "", "type": "commodity"}
        result = await c.process(item, source)
        assert "commodities" in result["topic_tags"]

    async def test_process_finance_type_index(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "f"
        cat.type = "finance"
        cat.slug = "finance"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "S&P 500", "summary": "", "type": "index"}
        result = await c.process(item, source)
        assert "market-indices" in result["topic_tags"]

    async def test_process_finance_fund(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "f"
        cat.type = "finance"
        cat.slug = "finance"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "ETF fund NAV", "summary": ""}
        result = await c.process(item, source)
        assert "fund-nav" in result["topic_tags"]

    async def test_process_finance_region_cn(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "f"
        cat.type = "finance"
        cat.slug = "finance"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "Stock", "summary": "", "type": "stock", "extra_data": {"region": "CN"}}
        result = await c.process(item, source)
        assert "china-stock" in result["topic_tags"]

    async def test_process_other_category(self):
        c = CategorizerProcessor()
        cat = MagicMock()
        cat.id = "o"
        cat.type = "other"
        cat.slug = "other"
        cat.keywords_filter = []
        source = _make_source(category=cat)
        item = {"title": "Random", "summary": ""}
        result = await c.process(item, source)
        assert result["topic_tags"] == ["other"]


# ── DedupProcessor ──────────────────────────────────────────────
class TestDedupProcessor:
    async def test_process_new_item(self):
        c = DedupProcessor()
        item = {"title": "Test Title", "url": "http://test.com"}
        source = _make_source()
        with (
            patch("app.processors.dedup.redis_sismember", new_callable=AsyncMock, return_value=False),
            patch("app.processors.dedup.redis_sadd", new_callable=AsyncMock),
        ):
            result = await c.process(item, source)
        assert result is not None
        assert "_dedup_hash" in result

    async def test_process_duplicate_item(self):
        c = DedupProcessor()
        item = {"title": "Test Title", "url": "http://test.com"}
        source = _make_source()
        with patch("app.processors.dedup.redis_sismember", new_callable=AsyncMock, return_value=True):
            result = await c.process(item, source)
        assert result is None

    async def test_process_redis_failure(self):
        c = DedupProcessor()
        item = {"title": "Test", "url": "http://test.com"}
        source = _make_source()
        with patch("app.processors.dedup.redis_sismember", new_callable=AsyncMock, side_effect=Exception("redis down")):
            result = await c.process(item, source)
        assert result is not None
        assert "_dedup_hash" in result

    async def test_content_hash(self):
        c = DedupProcessor()
        h = c._content_hash("title", "url")
        assert isinstance(h, str)
        assert len(h) == 32


# ── FilterProcessor ─────────────────────────────────────────────
class TestFilterProcessor:
    async def test_pass_valid_item(self):
        c = FilterProcessor()
        item = {"title": "A Long Valid Title Here", "summary": "Some content here that is long enough"}
        source = _make_source()
        result = await c.process(item, source)
        assert result is not None

    async def test_filter_short_title(self):
        c = FilterProcessor()
        item = {"title": "Hi", "summary": "content"}
        source = _make_source()
        result = await c.process(item, source)
        assert result is None

    async def test_filter_empty_title(self):
        c = FilterProcessor()
        item = {"title": "", "summary": "content"}
        source = _make_source()
        result = await c.process(item, source)
        assert result is None

    async def test_filter_blacklisted_keyword(self):
        c = FilterProcessor()
        item = {"title": "SPONSORED: great product", "summary": "Buy now"}
        source = _make_source()
        result = await c.process(item, source)
        assert result is None

    async def test_short_summary_sets_summary_to_title(self):
        c = FilterProcessor()
        item = {"title": "Valid Title That Passes", "summary": ""}
        source = _make_source()
        result = await c.process(item, source)
        assert result is not None
        assert result["summary"] == "Valid Title That Passes"

    async def test_short_summary_with_hn_score_passes(self):
        c = FilterProcessor()
        item = {"title": "Valid Short", "summary": "", "extra_data": {"hn_score": 100}}
        source = _make_source()
        result = await c.process(item, source)
        assert result is not None

    async def test_keyword_filter_matches(self):
        cat = MagicMock()
        cat.keywords_filter = ["python", "django"]
        source = _make_source(category=cat)
        c = FilterProcessor()
        item = {"title": "Python web framework", "summary": "Django is great"}
        result = await c.process(item, source)
        assert result is not None

    async def test_keyword_filter_no_match(self):
        cat = MagicMock()
        cat.keywords_filter = ["python", "django"]
        source = _make_source(category=cat)
        source.keywords_filter = ["python", "django"]
        c = FilterProcessor()
        item = {"title": "Rust programming language", "summary": "Fast and safe programming language"}
        result = await c.process(item, source)
        assert result is None

    async def test_source_keywords_override(self):
        cat = MagicMock()
        cat.keywords_filter = ["python"]
        source = _make_source(category=cat)
        source.keywords_filter = ["ruby"]
        c = FilterProcessor()
        item = {"title": "Ruby on Rails", "summary": "A framework for Ruby"}
        result = await c.process(item, source)
        assert result is not None

    async def test_no_keyword_filter(self):
        source = _make_source()
        source.keywords_filter = []
        source.category.keywords_filter = []
        c = FilterProcessor()
        item = {"title": "A Normal Valid Title", "summary": "Some content"}
        result = await c.process(item, source)
        assert result is not None


# ── TransformerProcessor ────────────────────────────────────────
class TestTransformerProcessor:
    async def test_process_full_item(self):
        c = TransformerProcessor()
        source = _make_source()
        item = {
            "title": "Test Article",
            "url": "http://example.com/article",
            "summary": "A good summary",
            "published_at": "2024-01-15T10:30:00Z",
            "extra_data": {"hn_score": 50},
        }
        result = await c.process(item, source)
        assert result is not None
        assert result["title"] == "Test Article"
        assert result["url"] == "http://example.com/article"
        assert "fetched_at" in result
        assert "2024-01-15" in result["published_at"]

    async def test_process_empty_title_returns_none(self):
        c = TransformerProcessor()
        source = _make_source()
        item = {"title": "", "url": "http://x.com"}
        result = await c.process(item, source)
        assert result is None

    async def test_process_empty_url_returns_none(self):
        c = TransformerProcessor()
        source = _make_source()
        item = {"title": "Valid Title", "url": ""}
        result = await c.process(item, source)
        assert result is None

    async def test_normalize_url_strips_tracking(self):
        c = TransformerProcessor()
        url = "http://Example.COM/path?utm_source=x&real_param=1"
        normalized = c._normalize_url(url)
        assert "utm_source" not in normalized
        assert "real_param" in normalized
        assert "example.com" in normalized

    async def test_normalize_url_adds_https(self):
        c = TransformerProcessor()
        normalized = c._normalize_url("example.com/page")
        assert normalized.startswith("https://")

    async def test_normalize_url_empty(self):
        c = TransformerProcessor()
        assert c._normalize_url("") == ""

    async def test_normalize_url_strip_trailing_slash(self):
        c = TransformerProcessor()
        normalized = c._normalize_url("http://example.com/path/")
        assert not normalized.endswith("/")

    async def test_normalize_url_exception(self):
        c = TransformerProcessor()
        result = c._normalize_url("http://" + "!" * 10000)
        assert isinstance(result, str)

    async def test_clean_html_summary(self):
        c = TransformerProcessor()
        raw = "<p>Hello &amp; <b>World</b></p>"
        clean = c._clean_html_summary(raw)
        assert "<" not in clean
        assert "&" in clean or "World" in clean

    async def test_clean_html_summary_long(self):
        c = TransformerProcessor()
        raw = "a" * 300
        clean = c._clean_html_summary(raw)
        assert clean.endswith("...")
        assert len(clean) <= 204

    def test_normalize_timestamp_empty(self):
        c = TransformerProcessor()
        result = c._normalize_timestamp("")
        assert isinstance(result, str)

    def test_normalize_timestamp_iso(self):
        c = TransformerProcessor()
        result = c._normalize_timestamp("2024-01-15T10:30:00Z")
        assert "2024-01-15" in result

    def test_normalize_timestamp_invalid(self):
        c = TransformerProcessor()
        result = c._normalize_timestamp("not-a-date")
        assert isinstance(result, str)

    def test_normalize_timestamp_various_formats(self):
        c = TransformerProcessor()
        r1 = c._normalize_timestamp("2024-01-15 10:30:00")
        assert "2024-01-15" in r1

        r2 = c._normalize_timestamp("Mon, 15 Jan 2024 10:30:00 +0000")
        assert "2024-01-15" in r2

    async def test_calculate_priority_bonuses(self):
        c = TransformerProcessor()
        source = MagicMock()
        source.priority = 3
        item = {
            "summary": "x" * 600,
            "published_at": "2099-01-01T00:00:00Z",
            "extra_data": {"hn_score": 200},
        }
        priority = c._calculate_priority(item, source)
        assert priority <= 10
        assert priority >= 3

    async def test_process_topic_tags_not_list(self):
        c = TransformerProcessor()
        source = _make_source()
        item = {
            "title": "Title",
            "url": "http://example.com",
            "summary": "Summary",
            "topic_tags": "not-a-list",
        }
        result = await c.process(item, source)
        assert result["topic_tags"] == []

    async def test_process_description_fallback(self):
        c = TransformerProcessor()
        source = _make_source()
        item = {
            "title": "Title",
            "url": "http://example.com",
            "description": "Description fallback",
        }
        result = await c.process(item, source)
        assert result is not None
        assert "Description" in result["summary"]
