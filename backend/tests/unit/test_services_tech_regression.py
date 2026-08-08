"""Regression tests for TechService missing-category guards.

A tenant without a seeded tech category used to crash with AttributeError on
tech_category.id (bare 500). get_news/get_topics must return empty success
responses instead, and must not run item/aggregation queries.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.tech import TechService


def _none_result():
    """A query result whose every extraction method reports 'not found'."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalar.return_value = 0
    return result


def _mock_db_without_tech_category():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_none_result())
    return db


class TestGetNewsMissingCategory:
    async def test_returns_empty_envelope_instead_of_attribute_error(self):
        db = _mock_db_without_tech_category()
        service = TechService(db, AsyncMock())

        result = await service.get_news(str(uuid.uuid4()))

        # Success envelope shape, not an exception
        assert result == {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

    async def test_empty_envelope_echoes_requested_pagination(self):
        db = _mock_db_without_tech_category()
        service = TechService(db, AsyncMock())

        result = await service.get_news("tenant-x", page=3, page_size=5)

        assert result["meta"] == {"total": 0, "page": 3, "page_size": 5}
        assert result["data"] == []

    async def test_no_item_query_runs_without_category(self):
        """Only the three category lookups execute; count/item queries never run."""
        db = _mock_db_without_tech_category()
        service = TechService(db, AsyncMock())

        await service.get_news("tenant-x", domain="ai", subcategory="llm", sort="time")

        assert db.execute.await_count == 3


class TestGetTopicsMissingCategory:
    async def test_returns_empty_list_instead_of_attribute_error(self):
        db = _mock_db_without_tech_category()
        service = TechService(db, AsyncMock())

        with (
            patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
            patch("app.services.tech.redis_set", AsyncMock()) as mock_redis_set,
        ):
            result = await service.get_topics(str(uuid.uuid4()))

        assert result == []
        # The jsonb aggregation query never runs...
        assert db.execute.await_count == 3
        # ...and nothing is cached for a failed lookup
        mock_redis_set.assert_not_called()

    async def test_missing_category_with_domain_filter(self):
        """The domain-filtered aggregation path must be guarded as well."""
        db = _mock_db_without_tech_category()
        service = TechService(db, AsyncMock())

        with (
            patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
            patch("app.services.tech.redis_set", AsyncMock()),
        ):
            result = await service.get_topics("tenant-x", domain="robotics")

        assert result == []
        assert db.execute.await_count == 3
