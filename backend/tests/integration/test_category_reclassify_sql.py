"""Real-SQL tests for CategoryService.reclassify_category_items (POST /categories/{id}/reclassify).

API-level integration tests mock the service, so the batch loop, the "only write
changed rows" rule and tenant scoping of the UPDATE path are exercised here
against a real database. No PG-specific SQL is involved (plain equality /
ORDER BY / OFFSET / LIMIT plus ORM updates), so this runs on the SQLite test
database too.

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
async def reclassify_env(db_session):
    """Seed: a system tech category holding system + tenant-A items (tenant scoping),
    a finance and a custom category for the tenants, and a bulk tech category with
    more rows than any single batch."""
    system_tenant = await db_session.get(Tenant, SYSTEM_TENANT_ID)
    if system_tenant is None:
        system_tenant = Tenant(name="System", slug=f"sys-{uuid.uuid4().hex[:8]}")
        system_tenant.id = SYSTEM_TENANT_ID
        db_session.add(system_tenant)

    suffix = uuid.uuid4().hex[:8]
    tenant_a = Tenant(name="Reclass A", slug=f"rca-{suffix}", plan="free", settings={})
    tenant_b = Tenant(name="Reclass B", slug=f"rcb-{suffix}", plan="free", settings={})
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    cat_tech_sys = Category(tenant_id=system_tenant.id, name="Tech Sys", slug=f"tech-{suffix}", type="tech")
    cat_fin_a = Category(tenant_id=tenant_a.id, name="Fin A", slug=f"fin-{suffix}", type="finance")
    cat_custom_b = Category(tenant_id=tenant_b.id, name="B Custom", slug=f"bcat-{suffix}", type="custom")
    cat_bulk_a = Category(tenant_id=tenant_a.id, name="Bulk A", slug=f"bulk-{suffix}", type="tech")
    cat_empty_a = Category(tenant_id=tenant_a.id, name="Empty A", slug=f"empty-{suffix}", type="custom")
    db_session.add_all([cat_tech_sys, cat_fin_a, cat_custom_b, cat_bulk_a, cat_empty_a])
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

    src_sys = make_source(system_tenant.id, cat_tech_sys.id, "Sys Tech RSS")
    src_a_on_sys = make_source(tenant_a.id, cat_tech_sys.id, "A on Sys RSS")
    src_fin_a = make_source(tenant_a.id, cat_fin_a.id, "Fin RSS")
    src_custom_b = make_source(tenant_b.id, cat_custom_b.id, "B RSS")
    src_bulk_a = make_source(tenant_a.id, cat_bulk_a.id, "Bulk RSS")
    db_session.add_all([src_sys, src_a_on_sys, src_fin_a, src_custom_b, src_bulk_a])
    await db_session.flush()

    now = datetime.now(UTC).replace(microsecond=0)
    published = now - timedelta(hours=1)

    def make_item(tenant_id, category_id, source_id, title, topic_tags, summary=""):
        return Item(
            tenant_id=tenant_id,
            category_id=category_id,
            source_id=source_id,
            title=title,
            summary=summary,
            url=f"https://example.com/{uuid.uuid4().hex}",
            topic_tags=topic_tags,
            priority=5,
            published_at=published,
            fetched_at=published,
        )

    # System-category rows: the system row must never be touched by a tenant's
    # reclassify; tenant A owns one stale row and one already-correct row.
    # Level-1 tag is the category slug (as written by the collector at ingest).
    item_sys = make_item(
        system_tenant.id,
        cat_tech_sys.id,
        src_sys.id,
        "GPT system item",
        [cat_tech_sys.slug, "stale-sys"],
    )
    item_a_stale = make_item(
        tenant_a.id,
        cat_tech_sys.id,
        src_a_on_sys.id,
        "GPT breakthrough",
        [cat_tech_sys.slug, "old-tag"],
    )
    item_a_correct = make_item(
        tenant_a.id,
        cat_tech_sys.id,
        src_a_on_sys.id,
        "Weekly digest",
        [cat_tech_sys.slug, "general"],
    )
    item_fin = make_item(tenant_a.id, cat_fin_a.id, src_fin_a.id, "黄金价格创新高", ["finance", "china-stock"])
    item_b = make_item(tenant_b.id, cat_custom_b.id, src_custom_b.id, "B item", ["stale"])
    items_bulk = [
        make_item(tenant_a.id, cat_bulk_a.id, src_bulk_a.id, f"ROS2 tooling {i}", ["stale"]) for i in range(7)
    ]
    db_session.add_all([item_sys, item_a_stale, item_a_correct, item_fin, item_b, *items_bulk])
    await db_session.flush()

    return {
        "session": db_session,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "cat_tech_sys": cat_tech_sys,
        "cat_fin_a": cat_fin_a,
        "cat_custom_b": cat_custom_b,
        "cat_bulk_a": cat_bulk_a,
        "cat_empty_a": cat_empty_a,
        "item_sys": item_sys,
        "item_a_stale": item_a_stale,
        "item_a_correct": item_a_correct,
        "item_fin": item_fin,
        "item_b": item_b,
        "items_bulk": items_bulk,
    }


def _service(session):
    return CategoryService(session, AsyncMock())


async def test_reclassify_overwrites_stale_tech_tags(reclassify_env):
    """The §5 scenario: KEYWORD_TO_TAG-style rules rebuilt over stored rows —
    only tenant-owned rows, and only when the tags actually change."""
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)
    cat = reclassify_env["cat_tech_sys"]

    result = await _service(session).reclassify_category_items(str(cat.id), tenant_a_id)

    assert result.data.scanned == 2
    assert result.data.updated == 1
    assert reclassify_env["item_a_stale"].topic_tags == [cat.slug, "ai", "llm"]
    # Already-correct row scanned but never rewritten
    assert reclassify_env["item_a_correct"].topic_tags == [cat.slug, "general"]
    # System-tenant rows are out of scope: a tenant reclassify never rewrites them
    assert reclassify_env["item_sys"].topic_tags == [cat.slug, "stale-sys"]


async def test_second_run_is_a_noop(reclassify_env):
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)
    cat = reclassify_env["cat_tech_sys"]

    service = _service(session)
    await service.reclassify_category_items(str(cat.id), tenant_a_id)
    second = await service.reclassify_category_items(str(cat.id), tenant_a_id)

    assert second.data.scanned == 2
    assert second.data.updated == 0


async def test_finance_rules_applied(reclassify_env):
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)
    cat = reclassify_env["cat_fin_a"]

    result = await _service(session).reclassify_category_items(str(cat.id), tenant_a_id)

    assert result.data.scanned == 1
    assert result.data.updated == 1
    assert reclassify_env["item_fin"].topic_tags == ["finance", "commodities"]


async def test_custom_category_resets_to_slug(reclassify_env):
    session = reclassify_env["session"]
    tenant_b_id = str(reclassify_env["tenant_b"].id)
    cat = reclassify_env["cat_custom_b"]

    result = await _service(session).reclassify_category_items(str(cat.id), tenant_b_id)

    assert result.data.updated == 1
    assert reclassify_env["item_b"].topic_tags == [cat.slug]


async def test_batching_covers_every_row(reclassify_env):
    """7 rows with batch_size=3 forces three loop iterations (3+3+1); every row
    must be scanned exactly once and rewritten."""
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)
    cat = reclassify_env["cat_bulk_a"]

    result = await _service(session).reclassify_category_items(str(cat.id), tenant_a_id, batch_size=3)

    assert result.data.scanned == 7
    assert result.data.updated == 7
    expected = [cat.slug, "robotics", "robot-software"]
    for item in reclassify_env["items_bulk"]:
        assert item.topic_tags == expected


async def test_empty_category_returns_zero_counts(reclassify_env):
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)
    cat = reclassify_env["cat_empty_a"]

    result = await _service(session).reclassify_category_items(str(cat.id), tenant_a_id)

    assert result.data.scanned == 0
    assert result.data.updated == 0


async def test_cross_tenant_category_returns_not_found(reclassify_env):
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)
    cat_b = reclassify_env["cat_custom_b"]

    with pytest.raises(CategoryNotFound):
        await _service(session).reclassify_category_items(str(cat_b.id), tenant_a_id)
    # Rejected before any rewrite
    assert reclassify_env["item_b"].topic_tags == ["stale"]


async def test_missing_category_returns_not_found(reclassify_env):
    session = reclassify_env["session"]
    tenant_a_id = str(reclassify_env["tenant_a"].id)

    with pytest.raises(CategoryNotFound):
        await _service(session).reclassify_category_items(str(uuid.uuid4()), tenant_a_id)
