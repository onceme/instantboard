import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tech import (
    TechService,
    DOMAIN_LABELS,
    SUBCATEGORY_TO_DOMAIN,
    TOPIC_TAG_LABELS,
    KEYWORD_TO_TAG,
    VALID_DOMAINS,
    HALF_LIFE_SECONDS,
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


class TestConstants:
    def test_domain_labels(self):
        assert "robotics" in DOMAIN_LABELS
        assert "ai" in DOMAIN_LABELS
        assert "embedded" in DOMAIN_LABELS
        assert "space" in DOMAIN_LABELS

    def test_valid_domains(self):
        assert VALID_DOMAINS == {"robotics", "ai", "embedded", "space"}

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
