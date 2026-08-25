"""Unit tests for ItemService (manual topic tag add/remove).

The session is mocked; real-SQL behaviour (tenant isolation, JSONB
read-modify-write) is covered in tests/integration/test_item_tags_sql.py.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import ItemNotFound, ValidationError
from app.services.item import MAX_ITEM_TAGS, USER_TAG_PATTERN, ItemService

OWNER_ID = uuid.uuid4()
OTHER_TENANT_ID = uuid.uuid4()


def _make_item(tenant_id=OWNER_ID, topic_tags=None):
    item = MagicMock()
    item.id = uuid.uuid4()
    item.tenant_id = tenant_id
    item.topic_tags = list(topic_tags) if topic_tags is not None else []
    return item


def _mock_db(item=None):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = item
    db.execute = AsyncMock(return_value=result)
    db.flush = AsyncMock()
    return db


class TestUserTagPattern:
    @pytest.mark.parametrize(
        "tag",
        ["a", "tag-1", "abc123", "a-b-c-1-2-3", "a" * 32],
    )
    def test_valid_formats(self, tag):
        assert USER_TAG_PATTERN.fullmatch(tag)

    @pytest.mark.parametrize(
        "tag",
        [
            "AI",  # uppercase
            "Tag-1",  # mixed case
            "a" * 33,  # too long
            "",  # empty
            "my_tag",  # underscore
            "my tag",  # space
            "中文标签",  # non-ascii
            "tag!",  # punctuation
            "tag.name",  # dot
            "-a" * 17,  # 34 chars
        ],
    )
    def test_invalid_formats(self, tag):
        assert not USER_TAG_PATTERN.fullmatch(tag)


class TestAddTagValidation:
    @pytest.mark.parametrize(
        "tag",
        ["UPPER", "under_score", "", "x" * 33, "空格"],
    )
    async def test_invalid_format_rejected_before_db_access(self, tag):
        item = _make_item(topic_tags=["tech"])
        db = _mock_db(item)
        service = ItemService(db)

        with pytest.raises(ValidationError):
            await service.add_tag(str(item.id), tag, str(OWNER_ID))

        db.execute.assert_not_called()
        db.flush.assert_not_called()
        assert item.topic_tags == ["tech"]

    async def test_invalid_format_error_details(self):
        db = _mock_db(_make_item())
        service = ItemService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.add_tag(str(uuid.uuid4()), "Bad Tag", str(OWNER_ID))

        assert exc_info.value.error_code == "VALIDATION_ERROR"
        assert exc_info.value.error_details[0]["field"] == "tag"


class TestAddTag:
    async def test_appends_after_existing_tags_preserving_order(self):
        item = _make_item(topic_tags=["tech", "ai", "llm"])
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.add_tag(str(item.id), "gpt-5", str(OWNER_ID))

        assert result.success is True
        assert result.data.item_id == str(item.id)
        assert result.data.topic_tags == ["tech", "ai", "llm", "gpt-5"]
        assert item.topic_tags == ["tech", "ai", "llm", "gpt-5"]
        db.flush.assert_called_once()

    async def test_none_topic_tags_treated_as_empty(self):
        item = _make_item(topic_tags=None)
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.add_tag(str(item.id), "first", str(OWNER_ID))

        assert result.data.topic_tags == ["first"]

    async def test_duplicate_tag_is_a_noop(self):
        item = _make_item(topic_tags=["tech", "ai", "llm"])
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.add_tag(str(item.id), "llm", str(OWNER_ID))

        assert result.data.topic_tags == ["tech", "ai", "llm"]
        db.flush.assert_not_called()

    async def test_duplicate_system_tag_is_a_noop(self):
        item = _make_item(topic_tags=["tech", "ai"])
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.add_tag(str(item.id), "tech", str(OWNER_ID))

        assert result.data.topic_tags == ["tech", "ai"]
        db.flush.assert_not_called()

    async def test_limit_reached_rejects_new_tag(self):
        full_tags = [f"tag-{i}" for i in range(MAX_ITEM_TAGS)]
        item = _make_item(topic_tags=full_tags)
        db = _mock_db(item)
        service = ItemService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.add_tag(str(item.id), "one-more", str(OWNER_ID))

        assert "limit" in exc_info.value.error_message.lower()
        assert item.topic_tags == full_tags
        db.flush.assert_not_called()

    async def test_limit_allows_duplicate_tag(self):
        full_tags = [f"tag-{i}" for i in range(MAX_ITEM_TAGS)]
        item = _make_item(topic_tags=full_tags)
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.add_tag(str(item.id), full_tags[0], str(OWNER_ID))

        assert result.data.topic_tags == full_tags
        db.flush.assert_not_called()

    async def test_one_below_limit_accepts(self):
        tags = [f"tag-{i}" for i in range(MAX_ITEM_TAGS - 1)]
        item = _make_item(topic_tags=tags)
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.add_tag(str(item.id), "last-one", str(OWNER_ID))

        assert result.data.topic_tags == tags + ["last-one"]


class TestRemoveTag:
    async def test_removes_tag_preserving_relative_order(self):
        item = _make_item(topic_tags=["tech", "ai", "llm", "my-note"])
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.remove_tag(str(item.id), "ai", str(OWNER_ID))

        assert result.data.topic_tags == ["tech", "llm", "my-note"]
        assert item.topic_tags == ["tech", "llm", "my-note"]
        db.flush.assert_called_once()

    async def test_removing_only_tag_leaves_empty_list(self):
        item = _make_item(topic_tags=["solo"])
        db = _mock_db(item)
        service = ItemService(db)

        result = await service.remove_tag(str(item.id), "solo", str(OWNER_ID))

        assert result.data.topic_tags == []

    async def test_tag_not_on_item_rejected(self):
        item = _make_item(topic_tags=["tech", "ai"])
        db = _mock_db(item)
        service = ItemService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.remove_tag(str(item.id), "missing", str(OWNER_ID))

        assert exc_info.value.error_details[0]["field"] == "tag"
        assert item.topic_tags == ["tech", "ai"]
        db.flush.assert_not_called()

    async def test_empty_tags_rejected(self):
        item = _make_item(topic_tags=[])
        db = _mock_db(item)
        service = ItemService(db)

        with pytest.raises(ValidationError):
            await service.remove_tag(str(item.id), "anything", str(OWNER_ID))


class TestTenantIsolationAndNotFound:
    async def test_missing_item_raises_not_found(self):
        db = _mock_db(item=None)
        service = ItemService(db)

        with pytest.raises(ItemNotFound):
            await service.add_tag(str(uuid.uuid4()), "tag", str(OWNER_ID))

        with pytest.raises(ItemNotFound):
            await service.remove_tag(str(uuid.uuid4()), "tag", str(OWNER_ID))

    async def test_cross_tenant_item_raises_not_found(self):
        item = _make_item(tenant_id=OTHER_TENANT_ID, topic_tags=["tech"])
        db = _mock_db(item)
        service = ItemService(db)

        with pytest.raises(ItemNotFound):
            await service.add_tag(str(item.id), "tag", str(OWNER_ID))

        with pytest.raises(ItemNotFound):
            await service.remove_tag(str(item.id), "tech", str(OWNER_ID))

        assert item.topic_tags == ["tech"]

    @pytest.mark.parametrize("bad_id", ["not-a-uuid", "", "12345", "g6f7e8d9-0000-0000-0000-000000000000"])
    async def test_malformed_item_id_raises_not_found_without_db(self, bad_id):
        db = _mock_db(_make_item())
        service = ItemService(db)

        with pytest.raises(ItemNotFound):
            await service.add_tag(bad_id, "tag", str(OWNER_ID))

        with pytest.raises(ItemNotFound):
            await service.remove_tag(bad_id, "tag", str(OWNER_ID))

        db.execute.assert_not_called()
