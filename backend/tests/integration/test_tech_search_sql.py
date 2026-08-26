"""Real-SQL tests for TechService.search_items (GET /api/v1/tech/search).

API-level integration tests mock the service, so the ILIKE keyword match, the
published_at DESC ordering, pagination and tenant isolation are exercised here
against a real database. ILIKE is portable (SQLite compiles it to
lower() LIKE lower()), so this runs on the SQLite test database too; the
JSONB-containment domain stack is PG-specific and covered by
test_pg_tech_system_tenant.py instead.

Everything runs inside the db_session fixture's transaction and is rolled
back, so no state persists.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete
from sqlalchemy.sql import sqltypes

from app.core.constants import SYSTEM_TENANT_ID
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.services.tech import TechService

# The production path binds the JWT's str tenant id against UUID columns
# (asyncpg accepts str). The SQLite test engine's Uuid bind processor calls
# value.hex and rejects str. Same idempotent lenient binding
# test_category_items_sql.py installs, replicated so this file also passes
# standalone.
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
async def search_env(db_session):
    """Seed a system tech category with keyword-staggered items across three
    tenants (matches the tech_env style of test_pg_tech_system_tenant.py).

    Rows committed by earlier tests may include other type='tech' categories,
    which would make _get_tech_category's OR-branch non-unique; clear the
    related tables inside this (rolled-back) transaction first.
    """
    from app.models.source import SourceHealth

    for model in (Item, SourceHealth, Source, Category):
        await db_session.execute(delete(model))
    await db_session.flush()

    system_tenant = await db_session.get(Tenant, SYSTEM_TENANT_ID)
    if system_tenant is None:
        system_tenant = Tenant(name="System", slug=f"sys-{uuid.uuid4().hex[:8]}")
        system_tenant.id = SYSTEM_TENANT_ID
        db_session.add(system_tenant)

    tenant_a = Tenant(name="Search A", slug=f"sa-{uuid.uuid4().hex[:8]}")
    tenant_b = Tenant(name="Search B", slug=f"sb-{uuid.uuid4().hex[:8]}")
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

    now = datetime.now(UTC).replace(microsecond=0)

    def make_item(tenant_id, title, summary, url_suffix, tags, hours_ago):
        return Item(
            tenant_id=tenant_id,
            category_id=category.id,
            source_id=source.id,
            title=title,
            summary=summary,
            url=f"https://example.com/{url_suffix}",
            topic_tags=tags,
            priority=5,
            published_at=now - timedelta(hours=hours_ago),
            fetched_at=now - timedelta(hours=hours_ago),
        )

    items = [
        # system-tenant row: title hit for "starlink", newest
        make_item(
            SYSTEM_TENANT_ID,
            "SpaceX Starlink launch recap",
            "Falcon 9 lifted off at dawn",
            "sys-starlink",
            ["tech", "space"],
            1,
        ),
        # tenant B row with the same keyword: must never leak into tenant A's search
        make_item(tenant_b.id, "Starlink competitor news", "Kuiper progresses", "b-starlink", ["tech", "space"], 2),
        # tenant A row: summary-only hit for "starlink"
        make_item(
            tenant_a.id,
            "Robot arm precision study",
            "A Starlink-sized antenna array was tested",
            "a-summary-hit",
            ["tech", "robotics"],
            3,
        ),
        # tenant A rows for ordering + pagination (all match "orbit")
        make_item(tenant_a.id, "Orbit report three", None, "a-orbit-3", ["tech"], 4),
        make_item(tenant_a.id, "Orbit report two", None, "a-orbit-2", ["tech"], 5),
        make_item(tenant_a.id, "Orbit report one", None, "a-orbit-1", ["tech"], 6),
        # tenant A row that matches nothing useful
        make_item(tenant_a.id, "GPT model survey", "large language models", "a-gpt", ["tech", "ai"], 7),
    ]
    db_session.add_all(items)
    await db_session.flush()

    return {
        "session": db_session,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
    }


def _service(session):
    """TechService with a stub redis: search_items never touches the cache."""
    return TechService(session, AsyncMock())


async def test_search_matches_title_case_insensitively(search_env):
    session = search_env["session"]
    tenant_a_id = str(search_env["tenant_a"].id)

    result = await _service(session).search_items(tenant_a_id, q="gpt")
    assert result["meta"]["total"] == 1
    assert result["data"][0]["title"] == "GPT model survey"

    upper = await _service(session).search_items(tenant_a_id, q="GPT")
    assert upper["meta"]["total"] == 1
    assert upper["data"][0]["title"] == "GPT model survey"


async def test_search_matches_summary_and_title_hits(search_env):
    """q=starlink matches the system item by title and the tenant A item by
    summary only; ordered by published_at DESC the system row comes first."""
    session = search_env["session"]
    tenant_a_id = str(search_env["tenant_a"].id)

    result = await _service(session).search_items(tenant_a_id, q="starlink")

    assert result["meta"]["total"] == 2
    assert [row["title"] for row in result["data"]] == [
        "SpaceX Starlink launch recap",
        "Robot arm precision study",
    ]
    # Envelope parity with the news feed: domain_tag inferred from topic_tags
    assert result["data"][0]["domain_tag"] == "space"
    assert result["data"][1]["source_name"] == "Seed RSS"


async def test_search_no_match_returns_empty_envelope(search_env):
    session = search_env["session"]
    tenant_a_id = str(search_env["tenant_a"].id)

    result = await _service(session).search_items(tenant_a_id, q="zzz-absent-term")

    assert result == {
        "data": [],
        "meta": {"total": 0, "page": 1, "page_size": 20},
    }


async def test_search_orders_by_published_at_desc(search_env):
    session = search_env["session"]
    tenant_a_id = str(search_env["tenant_a"].id)

    result = await _service(session).search_items(tenant_a_id, q="orbit")

    assert result["meta"]["total"] == 3
    assert [row["title"] for row in result["data"]] == [
        "Orbit report three",
        "Orbit report two",
        "Orbit report one",
    ]


async def test_search_paginates(search_env):
    session = search_env["session"]
    tenant_a_id = str(search_env["tenant_a"].id)

    first = await _service(session).search_items(tenant_a_id, q="orbit", page=1, page_size=2)
    assert first["meta"] == {"total": 3, "page": 1, "page_size": 2}
    assert [row["title"] for row in first["data"]] == ["Orbit report three", "Orbit report two"]

    second = await _service(session).search_items(tenant_a_id, q="orbit", page=2, page_size=2)
    assert second["meta"] == {"total": 3, "page": 2, "page_size": 2}
    assert [row["title"] for row in second["data"]] == ["Orbit report one"]

    beyond = await _service(session).search_items(tenant_a_id, q="orbit", page=3, page_size=2)
    assert beyond["data"] == []
    assert beyond["meta"]["total"] == 3


async def test_search_never_leaks_other_tenant_items(search_env):
    """Tenant B owns a 'Starlink competitor news' item: it must not appear in
    tenant A's search results, while the shared system-tenant row still does."""
    session = search_env["session"]
    tenant_a_id = str(search_env["tenant_a"].id)

    result = await _service(session).search_items(tenant_a_id, q="starlink")

    titles = {row["title"] for row in result["data"]}
    assert "Starlink competitor news" not in titles
    assert titles == {"SpaceX Starlink launch recap", "Robot arm precision study"}

    # Symmetric check from tenant B: sees its own row + the system row, not A's
    tenant_b_id = str(search_env["tenant_b"].id)
    other = await _service(session).search_items(tenant_b_id, q="starlink")
    assert {row["title"] for row in other["data"]} == {
        "Starlink competitor news",
        "SpaceX Starlink launch recap",
    }
