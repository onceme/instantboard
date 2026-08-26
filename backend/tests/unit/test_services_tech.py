import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.constants import SYSTEM_TENANT_ID
from app.services.tech import (
    DOMAIN_LABELS,
    HALF_LIFE_SECONDS,
    KEYWORD_TO_TAG,
    RELEVANCE_CANDIDATE_LIMIT,
    RELEVANCE_CANDIDATE_MULTIPLIER,
    RELEVANCE_TAG_BOOST,
    SUBCATEGORY_TO_DOMAIN,
    TOPIC_TAG_LABELS,
    VALID_DOMAINS,
    TechService,
)


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    return db, mock_result


def _mock_redis():
    return AsyncMock()


def _make_category(cat_id=None, slug="tech", type_="tech", tenant_id="tenant-1"):
    cat = MagicMock()
    cat.id = cat_id or uuid.uuid4()
    cat.slug = slug
    cat.type = type_
    cat.tenant_id = tenant_id
    cat.is_active = True
    return cat


def _make_item(
    item_id=None,
    tenant_id="tenant-1",
    category_id=None,
    source_id=None,
    title="Test News",
    summary="Summary",
    url="https://example.com/news",
    topic_tags=None,
    extra_data=None,
    priority=5,
    published_at=None,
    fetched_at=None,
    image_url=None,
):
    item = MagicMock()
    item.id = item_id or uuid.uuid4()
    item.tenant_id = tenant_id
    item.category_id = category_id or uuid.uuid4()
    item.source_id = source_id or uuid.uuid4()
    item.title = title
    item.summary = summary
    item.url = url
    item.topic_tags = topic_tags or ["tech"]
    item.extra_data = extra_data or {}
    item.priority = priority
    item.published_at = published_at or datetime.now(UTC)
    item.fetched_at = fetched_at or datetime.now(UTC)
    item.image_url = image_url
    source_mock = MagicMock()
    source_mock.name = "TestSource"
    item.source = source_mock
    return item


class TestGetNews:
    async def test_get_news_basic(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        item = _make_item(category_id=cat.id)

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 1
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = [item]
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1")
        assert result["meta"]["total"] == 1
        assert len(result["data"]) == 1
        assert result["data"][0]["title"] == "Test News"

    async def test_get_news_with_domain_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", domain="robotics")
        assert result["meta"]["total"] == 0

    async def test_get_news_with_subcategory_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", subcategory="humanoid")
        assert result["meta"]["total"] == 0

    async def test_get_news_sort_by_time(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", sort="time")
        assert result is not None

    async def test_get_news_sort_by_relevance(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", sort="relevance")
        assert result is not None

    async def test_get_news_hot_sort(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        item1 = _make_item(priority=10, published_at=datetime.now(UTC) - timedelta(hours=1))
        item2 = _make_item(priority=5, published_at=datetime.now(UTC))

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 2
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = [item1, item2]
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", sort="hot")
        assert len(result["data"]) == 2

    async def test_get_news_with_source_id(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", source_id=str(uuid.uuid4()))
        assert result is not None

    async def test_get_news_with_since_param(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", since="2024-01-01T00:00:00Z")
        assert result is not None

    async def test_get_news_invalid_since_param(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", since="invalid-date")
        assert result is not None

    async def test_get_news_pagination(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 50
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", page=2, page_size=10)
        assert result["meta"]["page"] == 2
        assert result["meta"]["page_size"] == 10

    async def test_get_news_item_no_source(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        item = _make_item()
        item.source = None

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 1
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = [item]
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1")
        assert result["data"][0]["source_name"] is None

    async def test_get_news_domain_invalid_skipped(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", domain="nonexistent")
        assert result is not None

    async def test_get_news_with_tag_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", tag="llm")
        assert result is not None

    async def test_get_news_with_tag_and_domain_stack(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", domain="ai", tag="llm")
        assert result is not None

    async def test_get_news_empty_tag_ignored(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                scalars = MagicMock()
                scalars.all.return_value = []
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_news("tenant-1", tag="")
        assert result["meta"]["total"] == 0


class TestGetTopics:
    @patch("app.services.tech.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.services.tech.redis_set", new_callable=AsyncMock)
    async def test_get_topics_no_filter(self, mock_set, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0
        row1 = ("humanoid", 10, datetime.now(UTC))
        row2 = ("llm", 5, None)

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.__iter__ = MagicMock(return_value=iter([row1, row2]))
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_topics("tenant-1")
        assert len(result) == 2
        assert result[0]["tag"] == "humanoid"
        assert result[0]["label"] == "人形机器人"
        assert result[1]["last_active_at"] is None

    @patch("app.services.tech.redis_get", new_callable=AsyncMock, return_value=None)
    @patch("app.services.tech.redis_set", new_callable=AsyncMock)
    async def test_get_topics_with_domain_filter(self, mock_set, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0
        row1 = ("humanoid", 10, datetime.now(UTC))

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.__iter__ = MagicMock(return_value=iter([row1]))
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_topics("tenant-1", domain="robotics")
        assert len(result) == 1

    @patch("app.services.tech.redis_get", new_callable=AsyncMock)
    async def test_get_topics_cached(self, mock_get):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        cached = json.dumps([{"tag": "humanoid", "label": "人形机器人", "count": 10, "last_active_at": None}])
        mock_get.return_value = cached

        mock_result.scalar_one_or_none.return_value = cat

        service = TechService(db, redis)
        result = await service.get_topics("tenant-1")
        assert len(result) == 1

    @patch("app.services.tech.redis_set", new_callable=AsyncMock)
    @patch("app.services.tech.redis_get", new_callable=AsyncMock)
    async def test_get_topics_invalid_cache(self, mock_get, mock_set):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        mock_get.return_value = "not-json{{{"

        call_count = 0
        row1 = ("humanoid", 10, datetime.now(UTC))

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.__iter__ = MagicMock(return_value=iter([row1]))
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service.get_topics("tenant-1")
        assert len(result) == 1


class TestExtractTopicTags:
    def test_extract_tags_with_keywords(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        tags = service.extract_topic_tags("GPT-4 is amazing", "Transformer based LLM")
        assert "ai" in tags
        assert "llm" in tags
        assert "tech" in tags

    def test_extract_tags_no_match(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        tags = service.extract_topic_tags("Some random content", "Nothing relevant")
        assert tags == ["tech"]

    def test_extract_tags_multiple_keywords(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        tags = service.extract_topic_tags("SpaceX launches Starlink satellites to orbit", None)
        assert "space" in tags
        assert "satellite-internet" in tags
        assert "commercial-space" in tags

    def test_extract_tags_no_summary(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        tags = service.extract_topic_tags("Atlas robot walks")
        assert "robotics" in tags
        assert "humanoid" in tags


class TestCalculateHotScore:
    def test_fresh_item_high_score(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        item = _make_item(priority=10, published_at=datetime.now(UTC))
        now = datetime.now(UTC)
        score = service._calculate_hot_score(item, now)
        assert score > 5

    def test_no_published_at(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        item = _make_item(priority=7)
        item.published_at = None
        now = datetime.now(UTC)
        score = service._calculate_hot_score(item, now)
        assert score == 7.0

    def test_future_published_at(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        item = _make_item(priority=5, published_at=datetime.now(UTC) + timedelta(hours=1))
        now = datetime.now(UTC)
        score = service._calculate_hot_score(item, now)
        assert score >= 5.0

    def test_with_hn_score(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        item = _make_item(priority=5, extra_data={"hn_score": 100}, published_at=datetime.now(UTC))
        now = datetime.now(UTC)
        score = service._calculate_hot_score(item, now)
        assert score > 5.0

    def test_old_item_low_score(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)

        item = _make_item(priority=10, published_at=datetime.now(UTC) - timedelta(days=3))
        now = datetime.now(UTC)
        score = service._calculate_hot_score(item, now)
        assert score < 10


class TestExtractDomainTag:
    def test_valid_domain(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)
        assert service._extract_domain_tag(["robotics", "humanoid"]) == "robotics"
        assert service._extract_domain_tag(["ai", "llm"]) == "ai"

    def test_subcategory_mapped(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)
        assert service._extract_domain_tag(["humanoid"]) == "robotics"
        assert service._extract_domain_tag(["llm"]) == "ai"
        assert service._extract_domain_tag(["iot-edge"]) == "embedded"
        assert service._extract_domain_tag(["commercial-space"]) == "space"

    def test_no_match(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)
        assert service._extract_domain_tag(["unknown-tag"]) is None

    def test_empty_tags(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = TechService(db, redis)
        assert service._extract_domain_tag(None) is None
        assert service._extract_domain_tag([]) is None


class TestGetTechCategory:
    async def test_find_by_tenant_and_slug(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()
        mock_result.scalar_one_or_none.return_value = cat

        service = TechService(db, redis)
        result = await service._get_tech_category("tenant-1")
        assert result == cat

    async def test_find_by_slug_only(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = cat
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service._get_tech_category("tenant-1")
        assert result == cat

    async def test_find_by_type_only(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count in (1, 2):
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = cat
            return mock_r

        db.execute = execute_side_effect

        service = TechService(db, redis)
        result = await service._get_tech_category("tenant-1")
        assert result == cat

    async def test_returns_none(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = TechService(db, redis)
        result = await service._get_tech_category("tenant-1")
        assert result is None


class TestExtractFavoriteTags:
    def test_none_and_empty(self):
        assert TechService._extract_favorite_tags(None) == []
        assert TechService._extract_favorite_tags({}) == []

    def test_non_list_favorite_tags(self):
        assert TechService._extract_favorite_tags({"favorite_tags": "llm"}) == []
        assert TechService._extract_favorite_tags({"favorite_tags": None}) == []

    def test_filters_non_string_and_empty_entries(self):
        prefs = {"favorite_tags": ["llm", "", None, 5, "drone"]}
        assert TechService._extract_favorite_tags(prefs) == ["llm", "drone"]


class TestRerankByFavoriteTags:
    def _item(self, title, priority, tags):
        item = MagicMock()
        item.title = title
        item.priority = priority
        item.topic_tags = tags
        return item

    def test_overlap_boost_moves_matching_items_first(self):
        high = self._item("high", 8, ["tech"])
        mid = self._item("mid", 7, ["tech", "robotics"])
        low = self._item("low", 5, ["tech", "llm", "ai"])

        ranked = TechService._rerank_by_favorite_tags([high, mid, low], ["llm", "ai"])

        # low: 5 + 2 overlaps * boost(2) = 9 > high 8 > mid 7
        assert [item.title for item in ranked] == ["low", "high", "mid"]

    def test_stable_order_on_equal_scores(self):
        a = self._item("a", 9, ["tech"])
        b = self._item("b", 8, ["llm"])
        c = self._item("c", 7, ["tech"])
        d = self._item("d", 6, ["llm", "ai"])

        ranked = TechService._rerank_by_favorite_tags([a, b, c, d], ["llm", "ai"])

        # b and d both score 10; candidate order (b before d) is preserved
        assert [item.title for item in ranked] == ["b", "d", "a", "c"]

    def test_empty_favorite_tags_keeps_candidate_order(self):
        a = self._item("a", 9, ["tech"])
        b = self._item("b", 5, ["llm"])

        ranked = TechService._rerank_by_favorite_tags([a, b], [])
        assert [item.title for item in ranked] == ["a", "b"]

    def test_missing_topic_tags_counts_no_overlap(self):
        a = self._item("a", 5, None)
        b = self._item("b", 4, ["llm"])

        ranked = TechService._rerank_by_favorite_tags([a, b], ["llm"])
        # a: 5 + 0, b: 4 + 2 -> b first
        assert [item.title for item in ranked] == ["b", "a"]


def _relevance_db(items, total, captured):
    """Mock session for get_news relevance flows: category -> count -> candidates.

    `captured` collects every executed statement so tests can inspect LIMIT/OFFSET.
    """
    db = AsyncMock()

    async def execute_side_effect(stmt, *args, **kwargs):
        captured.append(stmt)
        mock_r = MagicMock()
        if len(captured) == 1:
            mock_r.scalar_one_or_none.return_value = _make_category()
        elif len(captured) == 2:
            mock_r.scalar.return_value = total
        else:
            scalars = MagicMock()
            scalars.all.return_value = items
            mock_r.scalars.return_value = scalars
        return mock_r

    db.execute = execute_side_effect
    return db


class TestRelevanceRerank:
    def _items(self):
        return [
            _make_item(title="high", priority=8, topic_tags=["tech"]),
            _make_item(title="mid", priority=7, topic_tags=["tech", "robotics"]),
            _make_item(title="low", priority=5, topic_tags=["tech", "llm", "ai"]),
        ]

    async def test_overlap_items_move_first(self):
        items = self._items()
        captured = []
        db = _relevance_db(items, total=3, captured=captured)

        service = TechService(db, _mock_redis())
        result = await service.get_news("tenant-1", sort="relevance", user_preferences={"favorite_tags": ["llm", "ai"]})

        assert [row["title"] for row in result["data"]] == ["low", "high", "mid"]
        assert result["meta"]["total"] == 3

    async def test_no_preferences_keeps_priority_order(self):
        items = self._items()
        captured = []
        db = _relevance_db(items, total=3, captured=captured)

        service = TechService(db, _mock_redis())
        result = await service.get_news("tenant-1", sort="relevance")

        # Pure priority ordering: exactly the candidate order returned by SQL
        assert [row["title"] for row in result["data"]] == ["high", "mid", "low"]

    async def test_empty_favorite_tags_keeps_priority_order(self):
        items = self._items()
        captured = []
        db = _relevance_db(items, total=3, captured=captured)

        service = TechService(db, _mock_redis())
        result = await service.get_news("tenant-1", sort="relevance", user_preferences={"favorite_tags": []})

        assert [row["title"] for row in result["data"]] == ["high", "mid", "low"]

    async def test_reranked_pool_is_sliced_for_pagination(self):
        items = [
            _make_item(title="a", priority=9, topic_tags=["tech"]),
            _make_item(title="b", priority=8, topic_tags=["llm"]),
            _make_item(title="c", priority=7, topic_tags=["tech"]),
            _make_item(title="d", priority=6, topic_tags=["llm", "ai"]),
        ]
        captured = []
        db = _relevance_db(items, total=4, captured=captured)

        service = TechService(db, _mock_redis())
        prefs = {"favorite_tags": ["llm", "ai"]}

        page1 = await service.get_news("tenant-1", sort="relevance", page=1, page_size=2, user_preferences=prefs)
        assert [row["title"] for row in page1["data"]] == ["b", "d"]
        assert page1["meta"]["total"] == 4

        captured.clear()
        page2 = await service.get_news("tenant-1", sort="relevance", page=2, page_size=2, user_preferences=prefs)
        assert [row["title"] for row in page2["data"]] == ["a", "c"]

    @staticmethod
    def _sql(stmt) -> str:
        from sqlalchemy.dialects import postgresql

        return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    async def test_candidate_pool_limit_is_three_times_page_size(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.get_news("tenant-1", sort="relevance", page_size=20, user_preferences={"favorite_tags": ["llm"]})

        sql = self._sql(captured[-1])
        assert f"LIMIT {RELEVANCE_CANDIDATE_MULTIPLIER * 20}" in sql
        # Candidate fetch never applies OFFSET
        assert "OFFSET" not in sql

    async def test_candidate_pool_limit_capped(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.get_news("tenant-1", sort="relevance", page_size=150, user_preferences={"favorite_tags": ["llm"]})

        sql = self._sql(captured[-1])
        assert f"LIMIT {RELEVANCE_CANDIDATE_LIMIT}" in sql
        assert "OFFSET" not in sql

    async def test_no_preferences_query_keeps_offset_limit(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.get_news("tenant-1", sort="relevance", page=3, page_size=10)

        sql = self._sql(captured[-1])
        # Plain pagination: limit page_size, offset (page-1)*page_size
        assert "LIMIT 10" in sql
        assert f"OFFSET {(3 - 1) * 10}" in sql


class TestSearchItems:
    """search_items (GET /tech/search) — SQL construction, params, tenant scoping
    and the response envelope (design tech-tab.md §3.7)."""

    @staticmethod
    def _sql(stmt) -> str:
        from sqlalchemy.dialects import postgresql

        return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))

    def _captured_data_stmt(self, captured):
        """Execution order: 1) tech category lookup, 2) count, 3) paginated data."""
        assert len(captured) == 3
        return captured[-1]

    async def test_ilike_matches_title_or_summary(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.search_items("tenant-1", q="robot")

        sql = self._sql(self._captured_data_stmt(captured))
        # %% is the pyformat paramstyle doubling of literal % in str(compiled)
        assert "items.title ILIKE '%%robot%%'" in sql
        assert "items.summary ILIKE '%%robot%%'" in sql
        assert " OR " in sql

    async def test_query_is_stripped_into_pattern(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.search_items("tenant-1", q="  robot  ")

        sql = self._sql(self._captured_data_stmt(captured))
        assert "'%%robot%%'" in sql
        assert "'%%  robot  %%'" not in sql

    async def test_tenant_scoping_includes_system_tenant(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.search_items("tenant-1", q="robot")

        sql = self._sql(self._captured_data_stmt(captured))
        assert "items.tenant_id IN (" in sql
        assert "'tenant-1'" in sql
        # Shared system-tenant rows must stay visible (matches list_category_items)
        assert str(SYSTEM_TENANT_ID) in sql or SYSTEM_TENANT_ID.hex in sql

    async def test_domain_stacks_as_jsonb_containment(self):
        from sqlalchemy.dialects import postgresql

        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.search_items("tenant-1", q="robot", domain="ai")

        stmt = self._captured_data_stmt(captured)
        # JSONB has no literal value renderer, so compile with bind params and
        # inspect both the operator and the bound jsonb value.
        compiled = stmt.compile(dialect=postgresql.dialect())
        assert "items.topic_tags @>" in str(compiled)
        assert ["ai"] in list(compiled.params.values())

    async def test_invalid_domain_filter_skipped(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.search_items("tenant-1", q="robot", domain="not-a-domain")

        sql = self._sql(self._captured_data_stmt(captured))
        assert "@" not in sql

    async def test_orders_by_published_at_desc_with_pagination(self):
        captured = []
        db = _relevance_db([], total=0, captured=captured)

        service = TechService(db, _mock_redis())
        await service.search_items("tenant-1", q="robot", page=2, page_size=5)

        sql = self._sql(self._captured_data_stmt(captured))
        assert "ORDER BY items.published_at DESC" in sql
        assert "LIMIT 5" in sql
        assert "OFFSET 5" in sql

    async def test_missing_category_returns_empty_envelope(self):
        db = AsyncMock()
        mock_r = MagicMock()
        mock_r.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=mock_r)

        service = TechService(db, _mock_redis())
        result = await service.search_items("tenant-1", q="robot", page=3, page_size=7)

        assert result == {
            "data": [],
            "meta": {"total": 0, "page": 3, "page_size": 7},
        }

    async def test_envelope_maps_item_fields(self):
        item = _make_item(title="Robot news", topic_tags=["tech", "ai"], priority=6)

        captured = []
        db = _relevance_db([item], total=1, captured=captured)

        service = TechService(db, _mock_redis())
        result = await service.search_items("tenant-1", q="robot")

        assert result["meta"] == {"total": 1, "page": 1, "page_size": 20}
        row = result["data"][0]
        assert row["id"] == str(item.id)
        assert row["title"] == "Robot news"
        assert row["summary"] == "Summary"
        assert row["url"] == "https://example.com/news"
        assert row["source_name"] == "TestSource"
        assert row["source_id"] == str(item.source_id)
        assert row["category_id"] == str(item.category_id)
        assert row["topic_tags"] == ["tech", "ai"]
        assert row["domain_tag"] == "ai"
        assert row["priority"] == 6
        assert row["extra_data"] == {}
        assert isinstance(row["hot_score"], float)


class TestConstants:
    def test_domain_labels(self):
        assert "robotics" in DOMAIN_LABELS
        assert "ai" in DOMAIN_LABELS
        assert "embedded" in DOMAIN_LABELS
        assert "space" in DOMAIN_LABELS

    def test_valid_domains(self):
        assert {"robotics", "ai", "embedded", "space"} == VALID_DOMAINS

    def test_subcategory_to_domain(self):
        assert SUBCATEGORY_TO_DOMAIN["humanoid"] == "robotics"
        assert SUBCATEGORY_TO_DOMAIN["llm"] == "ai"
        assert SUBCATEGORY_TO_DOMAIN["iot-edge"] == "embedded"
        assert SUBCATEGORY_TO_DOMAIN["commercial-space"] == "space"

    def test_keyword_to_tag(self):
        assert "ai" in KEYWORD_TO_TAG["GPT"]
        assert "robotics" in KEYWORD_TO_TAG["Atlas"]
        assert "space" in KEYWORD_TO_TAG["SpaceX"]
        assert "embedded" in KEYWORD_TO_TAG["RISC-V"]

    def test_topic_tag_labels(self):
        assert TOPIC_TAG_LABELS["humanoid"] == "人形机器人"
        assert TOPIC_TAG_LABELS["llm"] == "大语言模型"

    def test_half_life_seconds(self):
        assert HALF_LIFE_SECONDS == 12 * 3600
