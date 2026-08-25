"""Real-SQL tests for ItemService.add_tag/remove_tag (POST/DELETE /items/{id}/tags).

API-level integration tests mock the service, so the JSONB read-modify-write,
the ordering guarantee and tenant isolation are exercised here against a real
database. No PG-specific SQL is involved (equality + ORM updates), so this
runs on the SQLite test database too.

Everything runs inside the db_session fixture's transaction and is rolled
back, so no state persists.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.sql import sqltypes

from app.core.exceptions import ItemNotFound, ValidationError
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.services.item import MAX_ITEM_TAGS, ItemService

# The production path binds the JWT's str tenant id / URL's str item id against
# UUID columns (asyncpg accepts str). The SQLite test engine's Uuid bind processor
# calls value.hex and rejects str. Same idempotent lenient binding test_api_sorting.py
# installs (and test_category_reclassify_sql.py), replicated here so this file
# also passes standalone.
if not getattr(sqltypes.Uuid.bind_processor, "_ib_accepts_str", False):
    _original_uuid_bind_processor = sqltypes.Uuid.bind_processor

    def _uuid_bind_processor_accepting_str(self, dialect):
        process = _original_uuid_bind_processor(self, dialect)
        if process is None:
            return None

        def process_lenient(value):
            if value is not None and not isinstance(value, uuid.UUID):
                value = uuid.UUID(str(value))
            return process(value)

        return process_lenient

    _uuid_bind_processor_accepting_str._ib_accepts_str = True  # type: ignore[attr-defined]
    sqltypes.Uuid.bind_processor = _uuid_bind_processor_accepting_str


@pytest.fixture
async def tag_env(db_session):
    """Seed two tenants, each with a category, a source and an item."""
    suffix = uuid.uuid4().hex[:8]
    tenant_a = Tenant(name="Tags A", slug=f"taga-{suffix}", plan="free", settings={})
    tenant_b = Tenant(name="Tags B", slug=f"tagb-{suffix}", plan="free", settings={})
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    cat_a = Category(tenant_id=tenant_a.id, name="Cat A", slug=f"cata-{suffix}", type="custom")
    cat_b = Category(tenant_id=tenant_b.id, name="Cat B", slug=f"catb-{suffix}", type="custom")
    db_session.add_all([cat_a, cat_b])
    await db_session.flush()

    def make_source(tenant_id, category_id, name):
        return Source(
            tenant_id=tenant_id,
            category_id=category_id,
            name=name,
            source_type="rss",
            url=f"https://{uuid.uuid4().hex[:8]}.example.com/rss",
            is_active=True,
        )

    src_a = make_source(tenant_a.id, cat_a.id, "RSS A")
    src_b = make_source(tenant_b.id, cat_b.id, "RSS B")
    db_session.add_all([src_a, src_b])
    await db_session.flush()

    published = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)

    def make_item(tenant_id, category_id, source_id, title, topic_tags):
        return Item(
            tenant_id=tenant_id,
            category_id=category_id,
            source_id=source_id,
            title=title,
            summary="",
            url=f"https://example.com/{uuid.uuid4().hex}",
            topic_tags=topic_tags,
            priority=5,
            published_at=published,
            fetched_at=published,
        )

    item_a = make_item(tenant_a.id, cat_a.id, src_a.id, "A item", ["tech", "ai", "llm"])
    item_b = make_item(tenant_b.id, cat_b.id, src_b.id, "B item", ["finance", "china-stock"])
    db_session.add_all([item_a, item_b])
    await db_session.flush()

    return {
        "session": db_session,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "item_a": item_a,
        "item_b": item_b,
    }


def _service(session):
    return ItemService(session)


async def test_add_tag_roundtrip_preserves_order(tag_env):
    session = tag_env["session"]
    item = tag_env["item_a"]
    tenant_a_id = str(tag_env["tenant_a"].id)

    result = await _service(session).add_tag(str(item.id), "gpt-5", tenant_a_id)

    assert result.data.topic_tags == ["tech", "ai", "llm", "gpt-5"]
    # Persisted on the ORM row: system tags first, user tag appended last
    assert item.topic_tags == ["tech", "ai", "llm", "gpt-5"]

    # Re-read from the database within the transaction to prove the flush landed
    await session.refresh(item)
    assert item.topic_tags == ["tech", "ai", "llm", "gpt-5"]


async def test_add_then_remove_roundtrip(tag_env):
    session = tag_env["session"]
    item = tag_env["item_a"]
    tenant_a_id = str(tag_env["tenant_a"].id)
    service = _service(session)

    await service.add_tag(str(item.id), "my-note", tenant_a_id)
    assert item.topic_tags == ["tech", "ai", "llm", "my-note"]

    removed = await service.remove_tag(str(item.id), "my-note", tenant_a_id)
    assert removed.data.topic_tags == ["tech", "ai", "llm"]
    assert item.topic_tags == ["tech", "ai", "llm"]


async def test_duplicate_add_is_persisted_noop(tag_env):
    session = tag_env["session"]
    item = tag_env["item_a"]
    tenant_a_id = str(tag_env["tenant_a"].id)
    service = _service(session)

    first = await service.add_tag(str(item.id), "dup", tenant_a_id)
    second = await service.add_tag(str(item.id), "dup", tenant_a_id)

    assert first.data.topic_tags == ["tech", "ai", "llm", "dup"]
    assert second.data.topic_tags == ["tech", "ai", "llm", "dup"]
    assert item.topic_tags.count("dup") == 1


async def test_add_tag_limit_enforced(tag_env):
    session = tag_env["session"]
    item = tag_env["item_a"]
    tenant_a_id = str(tag_env["tenant_a"].id)
    service = _service(session)

    # 3 seeded tags + 17 user tags = MAX_ITEM_TAGS
    for i in range(MAX_ITEM_TAGS - 3):
        await service.add_tag(str(item.id), f"user-{i}", tenant_a_id)
    assert len(item.topic_tags) == MAX_ITEM_TAGS

    with pytest.raises(ValidationError):
        await service.add_tag(str(item.id), "overflow", tenant_a_id)

    assert len(item.topic_tags) == MAX_ITEM_TAGS
    # Duplicate still allowed at the limit
    result = await service.add_tag(str(item.id), "user-0", tenant_a_id)
    assert result.data.topic_tags == item.topic_tags


async def test_invalid_tag_format_rejected(tag_env):
    session = tag_env["session"]
    item = tag_env["item_a"]
    tenant_a_id = str(tag_env["tenant_a"].id)

    with pytest.raises(ValidationError):
        await _service(session).add_tag(str(item.id), "Not-Lowercase", tenant_a_id)

    assert item.topic_tags == ["tech", "ai", "llm"]


async def test_remove_missing_tag_rejected(tag_env):
    session = tag_env["session"]
    item = tag_env["item_a"]
    tenant_a_id = str(tag_env["tenant_a"].id)

    with pytest.raises(ValidationError):
        await _service(session).remove_tag(str(item.id), "never-added", tenant_a_id)

    assert item.topic_tags == ["tech", "ai", "llm"]


async def test_cross_tenant_add_and_remove_rejected(tag_env):
    """Tenant A must not modify tenant B's item (both directions, 404 semantics)."""
    session = tag_env["session"]
    item_b = tag_env["item_b"]
    tenant_a_id = str(tag_env["tenant_a"].id)
    tenant_b_id = str(tag_env["tenant_b"].id)

    with pytest.raises(ItemNotFound):
        await _service(session).add_tag(str(item_b.id), "sneaky", tenant_a_id)

    with pytest.raises(ItemNotFound):
        await _service(session).remove_tag(str(item_b.id), "china-stock", tenant_a_id)

    # Tenant B's row untouched
    assert item_b.topic_tags == ["finance", "china-stock"]

    # And the reverse direction
    item_a = tag_env["item_a"]
    with pytest.raises(ItemNotFound):
        await _service(session).add_tag(str(item_a.id), "sneaky", tenant_b_id)


async def test_unknown_item_rejected(tag_env):
    session = tag_env["session"]
    tenant_a_id = str(tag_env["tenant_a"].id)

    with pytest.raises(ItemNotFound):
        await _service(session).add_tag(str(uuid.uuid4()), "tag", tenant_a_id)

    with pytest.raises(ItemNotFound):
        await _service(session).remove_tag(str(uuid.uuid4()), "tag", tenant_a_id)


async def test_malformed_item_id_rejected(tag_env):
    session = tag_env["session"]
    tenant_a_id = str(tag_env["tenant_a"].id)

    with pytest.raises(ItemNotFound):
        await _service(session).add_tag("not-a-uuid", "tag", tenant_a_id)

    with pytest.raises(ItemNotFound):
        await _service(session).remove_tag("not-a-uuid", "tag", tenant_a_id)
