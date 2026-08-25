"""Real-SQL tests for CategoryService.list_category_items (GET /categories/{id}/items).

API-level integration tests mock the service, so pagination/time ordering, the
`since` lower bound and tenant isolation are exercised here against a real
database. No PG-specific SQL is involved (plain equality/IN/ORDER BY/OFFSET),
so this runs on the SQLite test database too.

Everything runs inside the db_session fixture's transaction and is rolled
back, so no state persists.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.sql import sqltypes

from app.core.constants import SYSTEM_TENANT_ID
from app.core.exceptions import CategoryNotFound
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.services.category import CategoryService

# The production path binds the JWT's str tenant id / URL's str category id against
# UUID columns (asyncpg accepts str). The SQLite test engine's Uuid bind processor
# calls value.hex and rejects str. Same idempotent lenient binding test_api_sorting.py
# installs (and the e2e suite), replicated here so this file also passes standalone.
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
async def category_env(db_session):
    """Seed tenants A/B, a custom category of A with 3 staggered items, a system
    category with one item (shared-read case) and a private category of B."""
    system_tenant = await db_session.get(Tenant, SYSTEM_TENANT_ID)
    if system_tenant is None:
        system_tenant = Tenant(name="System", slug=f"sys-{uuid.uuid4().hex[:8]}")
        system_tenant.id = SYSTEM_TENANT_ID
        db_session.add(system_tenant)

    suffix = uuid.uuid4().hex[:8]
    tenant_a = Tenant(name="Cat Items A", slug=f"cat-a-{suffix}", plan="free", settings={})
    tenant_b = Tenant(name="Cat Items B", slug=f"cat-b-{suffix}", plan="free", settings={})
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    cat_custom = Category(tenant_id=tenant_a.id, name="Sports", slug=f"sports-{suffix}", type="custom")
    cat_sys = Category(tenant_id=system_tenant.id, name="Sys News", slug=f"sysnews-{suffix}", type="news")
    cat_b = Category(tenant_id=tenant_b.id, name="B Private", slug=f"bpriv-{suffix}", type="custom")
    db_session.add_all([cat_custom, cat_sys, cat_b])
    await db_session.flush()

    source_a = Source(
        tenant_id=tenant_a.id,
        category_id=cat_custom.id,
        name="Sports RSS",
        source_type="rss",
        url="https://sports.example.com/rss",
        is_active=True,
    )
    source_sys = Source(
        tenant_id=system_tenant.id,
        category_id=cat_sys.id,
        name="Sys RSS",
        source_type="rss",
        url="https://sysnews.example.com/rss",
        is_active=True,
    )
    source_b = Source(
        tenant_id=tenant_b.id,
        category_id=cat_b.id,
        name="B RSS",
        source_type="rss",
        url="https://b.example.com/rss",
        is_active=True,
    )
    db_session.add_all([source_a, source_sys, source_b])
    await db_session.flush()

    now = datetime.now(UTC).replace(microsecond=0)
    t_old = now - timedelta(hours=3)
    t_mid = now - timedelta(hours=2)
    t_new = now - timedelta(hours=1)

    def make_item(tenant_id, category_id, source_id, title, url, published_at):
        return Item(
            tenant_id=tenant_id,
            category_id=category_id,
            source_id=source_id,
            title=title,
            url=url,
            topic_tags=["general"],
            priority=5,
            published_at=published_at,
            fetched_at=published_at,
        )

    items = [
        make_item(tenant_a.id, cat_custom.id, source_a.id, "Old", "https://a.example.com/old", t_old),
        make_item(tenant_a.id, cat_custom.id, source_a.id, "Mid", "https://a.example.com/mid", t_mid),
        make_item(tenant_a.id, cat_custom.id, source_a.id, "New", "https://a.example.com/new", t_new),
        make_item(system_tenant.id, cat_sys.id, source_sys.id, "Sys", "https://sys.example.com/item", t_new),
        make_item(tenant_b.id, cat_b.id, source_b.id, "B Item", "https://b.example.com/item", t_new),
    ]
    db_session.add_all(items)
    await db_session.flush()

    return {
        "session": db_session,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "cat_custom": cat_custom,
        "cat_sys": cat_sys,
        "cat_b": cat_b,
        "t_old": t_old,
        "t_mid": t_mid,
        "t_new": t_new,
    }


def _service(session):
    return CategoryService(session, AsyncMock())


async def test_pagination_with_time_desc_ordering(category_env):
    session = category_env["session"]
    tenant_a_id = str(category_env["tenant_a"].id)
    cat = category_env["cat_custom"]

    first = await _service(session).list_category_items(str(cat.id), tenant_a_id, sort="time", page=1, page_size=2)
    assert first["meta"] == {"total": 3, "page": 1, "page_size": 2}
    assert [row["title"] for row in first["data"]] == ["New", "Mid"]

    second = await _service(session).list_category_items(str(cat.id), tenant_a_id, sort="time", page=2, page_size=2)
    assert [row["title"] for row in second["data"]] == ["Old"]


async def test_since_filters_older_items(category_env):
    session = category_env["session"]
    tenant_a_id = str(category_env["tenant_a"].id)
    cat = category_env["cat_custom"]

    # Lower bound between the oldest and the middle item: only mid/new survive
    since = (category_env["t_mid"] - timedelta(minutes=30)).isoformat()
    result = await _service(session).list_category_items(str(cat.id), tenant_a_id, sort="time", since=since)
    assert [row["title"] for row in result["data"]] == ["New", "Mid"]
    assert result["meta"]["total"] == 2

    # Lower bound strictly above the middle item: only the newest survives
    since = (category_env["t_mid"] + timedelta(seconds=1)).isoformat()
    result = await _service(session).list_category_items(str(cat.id), tenant_a_id, sort="time", since=since)
    assert [row["title"] for row in result["data"]] == ["New"]


async def test_invalid_since_is_ignored(category_env):
    session = category_env["session"]
    tenant_a_id = str(category_env["tenant_a"].id)
    cat = category_env["cat_custom"]

    result = await _service(session).list_category_items(str(cat.id), tenant_a_id, sort="time", since="not-a-date")
    assert result["meta"]["total"] == 3


async def test_system_tenant_items_visible_to_regular_tenant(category_env):
    session = category_env["session"]
    tenant_a_id = str(category_env["tenant_a"].id)
    cat_sys = category_env["cat_sys"]

    result = await _service(session).list_category_items(str(cat_sys.id), tenant_a_id, sort="time")
    assert [row["title"] for row in result["data"]] == ["Sys"]


async def test_cross_tenant_category_returns_not_found(category_env):
    session = category_env["session"]
    tenant_b_id = str(category_env["tenant_b"].id)
    cat_custom = category_env["cat_custom"]

    with pytest.raises(CategoryNotFound):
        await _service(session).list_category_items(str(cat_custom.id), tenant_b_id, sort="time")


async def test_missing_category_returns_not_found(category_env):
    session = category_env["session"]
    tenant_a_id = str(category_env["tenant_a"].id)

    with pytest.raises(CategoryNotFound):
        await _service(session).list_category_items(str(uuid.uuid4()), tenant_a_id, sort="time")


async def test_other_tenant_items_never_leak_into_feed(category_env):
    """The custom category feed must contain only that category's items even
    though other tenants' items exist in the table."""
    session = category_env["session"]
    tenant_a_id = str(category_env["tenant_a"].id)
    cat = category_env["cat_custom"]

    result = await _service(session).list_category_items(str(cat.id), tenant_a_id, sort="time")
    assert {"Old", "Mid", "New"} == {row["title"] for row in result["data"]}
