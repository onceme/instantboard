"""Real-DB tests for tenant scoping in DashboardService.get_data_source_health_detail.

Security regression tests: the detail query used to filter on source_id only, ignoring
the tenant_id parameter, so any admin could read source health details of other tenants.
The fixed query applies the same rule as the list endpoint: the source must belong to
the requesting tenant or to the system tenant; anything else yields None (404 at the
API layer).

Everything runs inside the db_session fixture's transaction and is rolled back, so no
state persists. Works on SQLite and PostgreSQL (no PG-specific SQL involved).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.core.constants import SYSTEM_TENANT_ID
from app.models.category import Category
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant
from app.services.dashboard import DashboardService


@pytest.fixture
async def source_env(db_session):
    """Seed three tenants (own / system / foreign), one healthy source per tenant,
    plus a foreign source WITHOUT a health record (fallback-path leak check)."""
    system_tenant = await db_session.get(Tenant, SYSTEM_TENANT_ID)
    if system_tenant is None:
        system_tenant = Tenant(name="System", slug=f"sys-{uuid.uuid4().hex[:8]}")
        system_tenant.id = SYSTEM_TENANT_ID
        db_session.add(system_tenant)

    suffix = uuid.uuid4().hex[:8]
    tenant_own = Tenant(name="Scope Own", slug=f"scope-own-{suffix}", plan="free", settings={})
    tenant_foreign = Tenant(name="Scope Foreign", slug=f"scope-for-{suffix}", plan="free", settings={})
    db_session.add_all([tenant_own, tenant_foreign])
    await db_session.flush()

    cat_own = Category(tenant_id=tenant_own.id, name="Own Cat", slug=f"own-{suffix}", type="news")
    cat_sys = Category(tenant_id=system_tenant.id, name="Sys Cat", slug=f"sys-{suffix}", type="news")
    cat_for = Category(tenant_id=tenant_foreign.id, name="For Cat", slug=f"for-{suffix}", type="news")
    db_session.add_all([cat_own, cat_sys, cat_for])
    await db_session.flush()

    source_own = Source(
        tenant_id=tenant_own.id,
        category_id=cat_own.id,
        name="Own Source",
        source_type="rss",
        url="https://own.example.com/rss",
        is_active=True,
    )
    source_sys = Source(
        tenant_id=system_tenant.id,
        category_id=cat_sys.id,
        name="System Source",
        source_type="rss",
        url="https://system.example.com/rss",
        is_active=True,
    )
    source_foreign = Source(
        tenant_id=tenant_foreign.id,
        category_id=cat_for.id,
        name="Foreign Source",
        source_type="rss",
        url="https://foreign.example.com/rss",
        is_active=True,
    )
    source_foreign_no_health = Source(
        tenant_id=tenant_foreign.id,
        category_id=cat_for.id,
        name="Foreign No Health",
        source_type="api",
        url="https://foreign.example.com/api",
        is_active=True,
    )
    db_session.add_all([source_own, source_sys, source_foreign, source_foreign_no_health])
    await db_session.flush()

    db_session.add_all(
        [
            SourceHealth(source_id=source_own.id, status="healthy"),
            SourceHealth(source_id=source_sys.id, status="healthy"),
            SourceHealth(source_id=source_foreign.id, status="healthy"),
        ]
    )
    await db_session.flush()

    return {
        "session": db_session,
        "tenant_own": tenant_own,
        "tenant_foreign": tenant_foreign,
        "source_own": source_own,
        "source_sys": source_sys,
        "source_foreign": source_foreign,
        "source_foreign_no_health": source_foreign_no_health,
    }


async def _detail(session, tenant_id, source_id):
    # uuid.UUID instances are passed intentionally (not str): SQLite's Uuid bind
    # processor requires uuid.UUID (it calls value.hex), while PostgreSQL accepts both.
    # The service signature stays str-typed for the API layer; passing UUID objects here
    # exercises the exact same WHERE clauses, so the scoping semantics under test are
    # unchanged.
    with patch("app.services.dashboard.redis_get", AsyncMock(return_value=None)):
        service = DashboardService(session, None)
        return await service.get_data_source_health_detail(tenant_id, source_id)


async def test_own_tenant_source_visible(source_env):
    detail = await _detail(source_env["session"], source_env["tenant_own"].id, source_env["source_own"].id)
    assert detail is not None
    assert detail["name"] == "Own Source"
    assert detail["status"] == "healthy"


async def test_system_tenant_source_visible(source_env):
    detail = await _detail(source_env["session"], source_env["tenant_own"].id, source_env["source_sys"].id)
    assert detail is not None
    assert detail["name"] == "System Source"


async def test_foreign_tenant_source_hidden(source_env):
    # The row exists and has a health record; only the tenant scope must reject it.
    detail = await _detail(source_env["session"], source_env["tenant_own"].id, source_env["source_foreign"].id)
    assert detail is None


async def test_foreign_tenant_source_without_health_hidden(source_env):
    # Guards the no-health fallback query, which must be tenant-scoped too (without the
    # fix it returned a synthesized "healthy" payload for other tenants' sources).
    detail = await _detail(
        source_env["session"], source_env["tenant_own"].id, source_env["source_foreign_no_health"].id
    )
    assert detail is None


async def test_missing_source_returns_none(source_env):
    detail = await _detail(source_env["session"], source_env["tenant_own"].id, uuid.uuid4())
    assert detail is None
