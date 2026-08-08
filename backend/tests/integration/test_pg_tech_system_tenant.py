"""Real-SQL verification of TechService, PostgreSQL only.

Unit tests mock the session and API-level integration tests mock TechService,
so the system-tenant IN clause and the jsonb topics aggregation would never be
executed against a real database otherwise. Here they are:

- get_news includes system-tenant items for a regular tenant while excluding
  other tenants' items (tenant_id IN (:tenant, :system) with a str tenant id
  bound against UUID columns — the asyncpg encoding regression is guarded)
- the domain filter uses the jsonb @> operator
- get_topics aggregates tags over tenant + system rows via
  jsonb_array_elements_text

Skipped when DATABASE_URL is not PostgreSQL (jsonb operators are PG-specific).
Everything runs inside the db_session fixture's transaction and is rolled
back, so no state persists.
"""
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete

from app.core.constants import SYSTEM_TENANT_ID
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.services.tech import TechService
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="jsonb operators (@>, jsonb_array_elements_text) require PostgreSQL",
)


@pytest.fixture
async def tech_env(db_session):
    """Seed an unambiguous tech category with items from three tenants.

    Rows committed by earlier tests may include other type='tech' categories,
    which would make _get_tech_category's OR-branch non-unique; clear the
    related tables inside this (rolled-back) transaction first.
    """
    from app.models.source import SourceHealth

    for model in (Item, SourceHealth, Source, Category):
        await db_session.execute(delete(model))
    await db_session.flush()

    now = datetime.now(UTC)

    # Earlier integration tests may have committed the system tenant; reuse it
    # instead of violating tenants_pkey.
    system_tenant = await db_session.get(Tenant, SYSTEM_TENANT_ID)
    if system_tenant is None:
        system_tenant = Tenant(name="System", slug=f"sys-{uuid.uuid4().hex[:8]}")
        system_tenant.id = SYSTEM_TENANT_ID
        db_session.add(system_tenant)
    tenant_a = Tenant(name="Tenant A", slug=f"ta-{uuid.uuid4().hex[:8]}")
    tenant_b = Tenant(name="Tenant B", slug=f"tb-{uuid.uuid4().hex[:8]}")
    db_session.add_all([tenant_a, tenant_b])
    await db_session.flush()

    category = Category(
        tenant_id=SYSTEM_TENANT_ID,
        name="科技",
        slug="tech",
        type="tech",
        is_active=True,
    )
    db_session.add(category)
    await db_session.flush()

    source = Source(
        tenant_id=SYSTEM_TENANT_ID,
        category_id=category.id,
        name="Seed RSS",
        source_type="rss",
        url="https://example.com/rss",
        is_active=True,
    )
    db_session.add(source)
    await db_session.flush()

    published = now - timedelta(hours=1)
    item_system = Item(
        tenant_id=SYSTEM_TENANT_ID,
        category_id=category.id,
        source_id=source.id,
        title="System tenant item",
        url="https://example.com/system-item",
        topic_tags=["tech", "ai"],
        priority=7,
        published_at=published,
    )
    item_a = Item(
        tenant_id=tenant_a.id,
        category_id=category.id,
        source_id=source.id,
        title="Tenant A item",
        url="https://example.com/tenant-a-item",
        topic_tags=["tech", "robotics"],
        priority=5,
        published_at=published,
    )
    item_b = Item(
        tenant_id=tenant_b.id,
        category_id=category.id,
        source_id=source.id,
        title="Tenant B item",
        url="https://example.com/tenant-b-item",
        topic_tags=["tech", "ai"],
        priority=5,
        published_at=published,
    )
    db_session.add_all([item_system, item_a, item_b])
    await db_session.flush()

    return {"session": db_session, "tenant_a": tenant_a, "category": category}


def _make_service(session):
    """TechService with redis backed out: only real SQL is under test."""
    return TechService(session, AsyncMock())


async def test_get_news_includes_system_tenant_items(tech_env):
    """Regular tenants must see system-tenant items alongside their own, and
    never other tenants' items. A plain str tenant id is bound against UUID
    columns (the asyncpg encoding regression this guards against)."""
    session = tech_env["session"]
    tenant_a_id = str(tech_env["tenant_a"].id)

    with (
        patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
        patch("app.services.tech.redis_set", AsyncMock()),
    ):
        result = await _make_service(session).get_news(tenant_a_id)

    titles = {row["title"] for row in result["data"]}
    assert titles == {"System tenant item", "Tenant A item"}
    assert result["meta"]["total"] == 2


# Regression guards for the fixed jsonb containment bug: get_news previously bound
# the @> operand as varchar (asyncpg: "operator does not exist: jsonb @> character
# varying"), 500'ing every domain/subcategory-filtered /tech/news request on
# PostgreSQL. get_news now filters via Item.topic_tags.contains([...]), which types the
# operand as jsonb ("@> :param::JSONB"). These must pass against real PostgreSQL.
async def test_get_news_domain_filter_uses_jsonb_containment(tech_env):
    """domain=ai maps to topic_tags @> '["ai"]' (jsonb containment). Only the
    system item carries the ai tag among the visible rows."""
    session = tech_env["session"]
    tenant_a_id = str(tech_env["tenant_a"].id)

    with (
        patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
        patch("app.services.tech.redis_set", AsyncMock()),
    ):
        result = await _make_service(session).get_news(tenant_a_id, domain="ai")

    assert result["meta"]["total"] == 1
    assert result["data"][0]["title"] == "System tenant item"


async def test_get_news_subcategory_filter_uses_jsonb_containment(tech_env):
    """Second occurrence of the containment filter (subcategory branch). Only the
    tenant A item carries the robotics tag among the visible rows."""
    session = tech_env["session"]
    tenant_a_id = str(tech_env["tenant_a"].id)

    with (
        patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
        patch("app.services.tech.redis_set", AsyncMock()),
    ):
        result = await _make_service(session).get_news(tenant_a_id, subcategory="robotics")

    assert result["meta"]["total"] == 1
    assert result["data"][0]["title"] == "Tenant A item"


async def test_get_topics_aggregates_tenant_and_system_rows_only(tech_env):
    """The jsonb_array_elements_text aggregation must count tenant + system
    items only: tech=2 and ai=1 (tenant B's ai item excluded), robotics=1."""
    session = tech_env["session"]
    tenant_a_id = str(tech_env["tenant_a"].id)

    with (
        patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
        patch("app.services.tech.redis_set", AsyncMock()),
    ):
        topics = await _make_service(session).get_topics(tenant_a_id)

    counts = {t["tag"]: t["count"] for t in topics}
    assert counts["tech"] == 2
    assert counts["robotics"] == 1
    # Would be 2 if tenant B's rows leaked into the aggregation
    assert counts["ai"] == 1
    # Labels resolved through TOPIC_TAG_LABELS
    by_tag = {t["tag"]: t for t in topics}
    assert by_tag["ai"]["label"] == "人工智能"


async def test_get_topics_with_domain_filter_on_real_jsonb(tech_env):
    """domain-filtered variant of the aggregation (extra @> predicate)."""
    session = tech_env["session"]
    tenant_a_id = str(tech_env["tenant_a"].id)

    with (
        patch("app.services.tech.redis_get", AsyncMock(return_value=None)),
        patch("app.services.tech.redis_set", AsyncMock()),
    ):
        topics = await _make_service(session).get_topics(tenant_a_id, domain="ai")

    counts = {t["tag"]: t["count"] for t in topics}
    # Only items tagged ai are in scope: the system item contributes tech + ai
    assert counts["ai"] == 1
    assert counts["tech"] == 1
    assert "robotics" not in counts
