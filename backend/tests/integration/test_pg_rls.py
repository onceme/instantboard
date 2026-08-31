"""Real-PostgreSQL verification of the tenancy Row-Level Security migration
(database.md §3.5).

What the unit tests cannot prove, against a real PostgreSQL:

- the migration enables ENABLE + FORCE RLS with the ``tenant_isolation``
  policy on exactly the 8 tenant-scoped tables (``users`` /
  ``dashboard_snapshots`` stay untouched), including the current partitions
  of ``finance_quotes``;
- a role WITHOUT superuser/BYPASSRLS is fenced by the policies:
  a) no GUC set            → zero rows visible, INSERT rejected (default deny)
  b) ``app.current_tenant_id=A`` → only tenant A rows + shared system-tenant
     rows visible; tenant-B rows not updatable/deletable; writes to
     another tenant or the system tenant rejected by WITH CHECK
  c) ``app.is_service=on`` → full visibility and write access (the service
     bypass used by every background session)
  d) partition direct access obeys the policy (partitions carry their own
     ENABLE/FORCE/policy — they do not inherit the parent's flags)
- the session context wiring works end to end: ``bind_tenant_context`` (the
  request path), ``apply_service_context`` (the background path), and
  transaction-local GUCs never leaking into the next session;
- ``scheduler_manager._run_collection`` still stores items on an RLS-enabled
  database — proof that the background service-context wiring is complete
  (the owner role is FORCE-subjected to the policy; without the bypass the
  collection INSERT would be rejected).

The suite runs on its own throwaway database with a dedicated role that is
the table OWNER yet neither superuser nor BYPASSRLS (created/dropped around
the tests, objects transferred via REASSIGN OWNED BY). This mirrors the
production setup — single app role owning the tables — and makes FORCE ROW
LEVEL SECURITY observable: without FORCE (or for a superuser) every
assertion here would pass vacuously. Superusers always bypass RLS, so the
isolation checks are only meaningful for a non-superuser owner role —
production roles must not be superusers/BYPASSRLS (database.md §3.5).
Skipped when DATABASE_URL is not PostgreSQL.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.config import settings
from app.db.partitions import ensure_quote_partitions, quote_partition_name
from app.db.rls import RLS_TABLES, TENANT_ISOLATION_POLICY
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="RLS migration tests require PostgreSQL",
)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "app" / "alembic" / "alembic.ini"

TENANT_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
SYSTEM_TENANT = uuid.UUID("00000000-0000-0000-0000-000000000000")

USER_A = uuid.UUID("aaaaaaaa-1111-1111-1111-111111111111")
USER_B = uuid.UUID("bbbbbbbb-1111-1111-1111-111111111111")
CAT_A = uuid.UUID("11111111-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CAT_B = uuid.UUID("22222222-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
CAT_SYS = uuid.UUID("33333333-cccc-cccc-cccc-cccccccccccc")
SRC_A = uuid.UUID("44444444-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
ITEM_A = uuid.UUID("55555555-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SYM_A = uuid.UUID("66666666-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SYM_B = uuid.UUID("66666666-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

SEED_STATEMENTS: list[tuple[str, dict]] = [
    (
        "INSERT INTO tenants (id, name, slug) VALUES (:id, :name, :slug)",
        [
            {"id": TENANT_A, "name": "Tenant A", "slug": "rls-it-a"},
            {"id": TENANT_B, "name": "Tenant B", "slug": "rls-it-b"},
            {"id": SYSTEM_TENANT, "name": "System", "slug": "rls-it-system"},
        ],
    ),
    (
        "INSERT INTO users (id, tenant_id, email, name, sso_provider, sso_provider_id) "
        "VALUES (:id, :tenant_id, :email, :name, 'github', :sso_provider_id)",
        [
            {"id": USER_A, "tenant_id": TENANT_A, "email": "a@rls-it.test", "name": "A", "sso_provider_id": "rls-it-a"},
            {"id": USER_B, "tenant_id": TENANT_B, "email": "b@rls-it.test", "name": "B", "sso_provider_id": "rls-it-b"},
        ],
    ),
    (
        "INSERT INTO categories (id, tenant_id, name, slug, type) VALUES (:id, :tenant_id, :name, :slug, 'news')",
        [
            {"id": CAT_A, "tenant_id": TENANT_A, "name": "Cat A", "slug": "rls-cat-a"},
            {"id": CAT_B, "tenant_id": TENANT_B, "name": "Cat B", "slug": "rls-cat-b"},
            {"id": CAT_SYS, "tenant_id": SYSTEM_TENANT, "name": "Cat System", "slug": "rls-cat-sys"},
        ],
    ),
    (
        "INSERT INTO sources (id, tenant_id, category_id, name, source_type, url) "
        "VALUES (:id, :tenant_id, :category_id, :name, 'rss', :url)",
        [
            {
                "id": SRC_A,
                "tenant_id": TENANT_A,
                "category_id": CAT_A,
                "name": "RLS IT Source A",
                "url": "https://example.com/rls-it-a.xml",
            },
        ],
    ),
    (
        "INSERT INTO items (id, tenant_id, category_id, source_id, title, url, published_at) "
        "VALUES (:id, :tenant_id, :category_id, :source_id, :title, :url, :published_at)",
        [
            {
                "id": ITEM_A,
                "tenant_id": TENANT_A,
                "category_id": CAT_A,
                "source_id": SRC_A,
                "title": "RLS IT item A",
                "url": "https://example.com/rls-item-a",
                "published_at": datetime.now(UTC),
            },
        ],
    ),
    (
        "INSERT INTO finance_symbols (id, tenant_id, symbol, name, type, market) "
        "VALUES (:id, :tenant_id, :symbol, :name, 'index', 'GLOBAL')",
        [
            {"id": SYM_A, "tenant_id": TENANT_A, "symbol": "RLSA", "name": "RLS A"},
            {"id": SYM_B, "tenant_id": TENANT_B, "symbol": "RLSB", "name": "RLS B"},
        ],
    ),
]


def _with_database(url: str, database: str) -> str:
    # NOTE: str(URL) masks the password as '***'; render_as_string keeps it.
    return make_url(url).set(database=database).render_as_string(hide_password=False)


def _probe_url(url: str, role: str, password: str) -> str:
    return make_url(url).set(username=role, password=password).render_as_string(hide_password=False)


def _maintenance_engine(database: str):
    # CREATE/DROP DATABASE cannot run inside a transaction block.
    return create_async_engine(
        _with_database(TEST_DATABASE_URL, database),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )


def _assert_rls_violation(exc: Exception, table: str) -> None:
    message = str(exc)
    assert "row-level security" in message, f"expected an RLS violation for {table}, got: {message}"


@pytest.fixture(scope="module")
def rls_db():
    """A throwaway database upgraded to head, partitioned, RLS-protected, with
    two tenants seeded plus a dedicated non-privileged probe role."""
    db_name = f"rls_it_{uuid.uuid4().hex[:12]}"
    role_suffix = uuid.uuid4().hex[:8]
    role_name = f"rls_probe_{role_suffix}"
    role_password = "rls_probe_pass"
    # The application-role analogue: it creates (and thus owns) every table so
    # that FORCE ROW LEVEL SECURITY is genuinely exercised — this role connects
    # as the owner without superuser/BYPASSRLS, exactly like the production app
    # role. The superuser bootstrap role would bypass RLS outright and mask
    # any wiring gap.
    owner_role_name = f"rls_owner_{role_suffix}"
    owner_role_password = "rls_owner_pass"
    test_url = _with_database(TEST_DATABASE_URL, db_name)
    probe_connection_url = _probe_url(test_url, role_name, role_password)
    owner_connection_url = _probe_url(test_url, owner_role_name, owner_role_password)

    async def _create_database() -> None:
        maintenance = _maintenance_engine("postgres")
        try:
            async with maintenance.connect() as conn:
                await conn.execute(text(f"CREATE DATABASE {db_name}"))
        finally:
            await maintenance.dispose()

    asyncio.run(_create_database())

    async def _create_roles() -> None:
        maintenance = _maintenance_engine("postgres")
        try:
            async with maintenance.connect() as conn:
                await conn.execute(text(f"CREATE ROLE {role_name} LOGIN PASSWORD '{role_password}'"))
                await conn.execute(text(f"CREATE ROLE {owner_role_name} LOGIN PASSWORD '{owner_role_password}'"))
        finally:
            await maintenance.dispose()

    asyncio.run(_create_roles())

    # env.py reads the database URL from app settings — point the singleton at
    # the throwaway database for the duration of this module.
    original_url = settings.database_url
    settings.database_url = test_url

    # Make the owner role the actual creator (and thus owner) of every object:
    # grant it CREATE on schema public, then run the migration under its URL.
    # This mirrors production — a single non-superuser app role owning the
    # tables — so FORCE ROW LEVEL SECURITY is genuinely exercised.
    async def _grant_schema_create() -> None:
        engine = create_async_engine(test_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"GRANT CREATE ON SCHEMA public TO {owner_role_name}"))
        finally:
            await engine.dispose()

    asyncio.run(_grant_schema_create())

    settings.database_url = owner_connection_url

    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent))
    cfg.set_main_option("sqlalchemy.url", owner_connection_url)
    # Schema (baseline + RLS migration) and month partitions are created by
    # the owner role itself; afterwards it is subject to its own policies
    # because of FORCE ROW LEVEL SECURITY.
    command.upgrade(cfg, "head")
    asyncio.run(ensure_quote_partitions())

    # Seed + probe grants run as the superuser bootstrap role: superusers
    # bypass RLS (inserts land regardless of the policies), while the probe
    # role afterwards only ever goes through the policies.
    async def _grant_and_seed() -> None:
        engine = create_async_engine(test_url, poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(f"GRANT ALL ON ALL TABLES IN SCHEMA public TO {role_name}"))
                await conn.execute(text(f"GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO {role_name}"))
            for statement, rows in SEED_STATEMENTS:
                for params in rows:
                    async with engine.begin() as conn:
                        await conn.execute(text(statement), params)
        finally:
            await engine.dispose()

    asyncio.run(_grant_and_seed())

    try:
        yield {
            "db_name": db_name,
            "role_name": role_name,
            "owner_role_name": owner_role_name,
            "url": test_url,
            "probe_url": probe_connection_url,
            "owner_url": owner_connection_url,
            "cfg": cfg,
        }
    finally:
        settings.database_url = original_url

        async def _teardown() -> None:
            maintenance = _maintenance_engine("postgres")
            try:
                async with maintenance.connect() as conn:
                    await conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
                    await conn.execute(text(f"DROP ROLE IF EXISTS {role_name}"))
                    await conn.execute(text(f"DROP ROLE IF EXISTS {owner_role_name}"))
            finally:
                await maintenance.dispose()

        asyncio.run(_teardown())


def _probe_session(rls_db, poolclass=None, **engine_kwargs):
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    engine = create_async_engine(rls_db["probe_url"], poolclass=poolclass or NullPool, **engine_kwargs)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory


# ---------------------------------------------------------------------------
# migration result: flags + policies
# ---------------------------------------------------------------------------


def test_rls_enabled_and_forced_on_exactly_the_core_tables(rls_db) -> None:
    async def _flags(engine, names: list[str]) -> dict[str, tuple[bool, bool, int]]:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "(SELECT count(*) FROM pg_policy p "
                        " WHERE p.polrelid = c.oid AND p.polname = :policy) "
                        "FROM pg_class c WHERE c.relname = ANY(:names)"
                    ),
                    {"policy": TENANT_ISOLATION_POLICY, "names": names},
                )
            ).all()
        return {name: (rls, force, count) for name, rls, force, count in rows}

    async def _run() -> None:
        engine = create_async_engine(rls_db["url"], poolclass=NullPool)
        try:
            now = datetime.now(UTC)
            partitions = [
                quote_partition_name(now.year, now.month),
            ]
            core = await _flags(engine, list(RLS_TABLES))
            for table in RLS_TABLES:
                rls, force, count = core[table]
                assert rls, f"{table}: ENABLE ROW LEVEL SECURITY missing"
                assert force, f"{table}: FORCE ROW LEVEL SECURITY missing"
                assert count == 1, f"{table}: tenant_isolation policy missing"

            parts = await _flags(engine, partitions)
            for partition in partitions:
                assert partition in parts, f"{partition} was not created"
                rls, force, count = parts[partition]
                assert rls and force and count == 1, f"{partition}: RLS not wired at partition level"

            excluded = await _flags(engine, ["users", "dashboard_snapshots", "tenants", "source_health"])
            for table, (rls, force, count) in excluded.items():
                assert not rls and not force and count == 0, f"{table}: must stay RLS-free"
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# probe-role isolation semantics
# ---------------------------------------------------------------------------


def test_no_guc_default_deny(rls_db) -> None:
    """Without any GUC a non-privileged session sees nothing and writes are
    rejected by WITH CHECK on every RLS table."""

    async def _run() -> None:
        engine, factory = _probe_session(rls_db)
        try:
            async with factory() as session:
                for table in RLS_TABLES:
                    count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
                    assert count == 0, f"{table}: rows visible without any GUC"
                await session.rollback()

            async with factory() as session:
                with pytest.raises(Exception) as excinfo:
                    async with session.begin():
                        await session.execute(
                            text(
                                "INSERT INTO categories (id, tenant_id, name, slug, type) "
                                "VALUES (gen_random_uuid(), :tenant_id, 'sneak', 'rls-it-sneak', 'news')"
                            ),
                            {"tenant_id": TENANT_A},
                        )
                _assert_rls_violation(excinfo.value, "categories")
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_tenant_guc_visibility_scoping(rls_db) -> None:
    """``bind_tenant_context`` (the request-path wiring) yields exactly the
    caller's rows plus the shared system-tenant rows."""
    from app.db.rls import bind_tenant_context

    async def _run() -> None:
        engine, factory = _probe_session(rls_db)
        try:
            async with factory() as session:
                bind_tenant_context(session, str(TENANT_A))

                slugs = (await session.execute(text("SELECT slug FROM categories ORDER BY slug"))).scalars().all()
                assert slugs == ["rls-cat-a", "rls-cat-sys"], slugs

                items = (await session.execute(text("SELECT count(*) FROM items"))).scalar()
                assert items == 1  # tenant A row; system items were not seeded
                symbols = (await session.execute(text("SELECT symbol FROM finance_symbols"))).scalars().all()
                assert symbols == ["RLSA"], symbols
                for table in ("sources", "watchlist_items", "sse_connections", "fund_nav_estimates"):
                    count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar()
                    # sources: only tenant A's (none seeded for system)
                    # watchlist_items / sse_connections / fund_nav_estimates: none seeded yet
                    assert count in (0, 1), f"{table}: unexpected cross-tenant visibility"
                quotes = (await session.execute(text("SELECT count(*) FROM finance_quotes"))).scalar()
                assert quotes == 0  # no quote seeded for tenant A
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_tenant_guc_cannot_touch_other_tenants(rls_db) -> None:
    from app.db.rls import bind_tenant_context

    async def _run() -> None:
        engine, factory = _probe_session(rls_db)
        try:
            async with factory() as session:
                bind_tenant_context(session, str(TENANT_A))
                updated = await session.execute(
                    text("UPDATE categories SET name = 'hacked' WHERE tenant_id = :other"),
                    {"other": TENANT_B},
                )
                assert updated.rowcount == 0
                deleted = await session.execute(
                    text("DELETE FROM items WHERE tenant_id = :other"),
                    {"other": TENANT_B},
                )
                assert deleted.rowcount == 0
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_tenant_guc_write_check_rules(rls_db) -> None:
    """WITH CHECK allows own-tenant writes only: other tenants and the shared
    system tenant (read-only by design) are rejected."""
    from app.db.rls import bind_tenant_context

    async def _insert(session, tenant_id: uuid.UUID, slug: str) -> None:
        async with session.begin_nested():
            await session.execute(
                text(
                    "INSERT INTO categories (id, tenant_id, name, slug, type) "
                    "VALUES (gen_random_uuid(), :tenant_id, :name, :slug, 'news')"
                ),
                {"tenant_id": tenant_id, "name": f"rls-it-{slug}", "slug": slug},
            )

    async def _run() -> None:
        engine, factory = _probe_session(rls_db)
        try:
            async with factory() as session:
                bind_tenant_context(session, str(TENANT_A))
                # own tenant: accepted
                await _insert(session, TENANT_A, "rls-it-own-write")
                # another tenant: rejected
                with pytest.raises(Exception) as excinfo:
                    await _insert(session, TENANT_B, "rls-it-cross-write")
                _assert_rls_violation(excinfo.value, "categories")
                # system tenant: rejected (WITH CHECK carries no SYSTEM clause)
                with pytest.raises(Exception) as excinfo:
                    await _insert(session, SYSTEM_TENANT, "rls-it-system-write")
                _assert_rls_violation(excinfo.value, "categories")
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_service_guc_is_a_full_bypass(rls_db) -> None:
    """``apply_service_context`` (the background-path wiring) restores full
    visibility and write access — including the system tenant."""
    from app.db.session import apply_service_context

    async def _run() -> None:
        engine, factory = _probe_session(rls_db)
        try:
            async with factory() as session:
                await apply_service_context(session)
                categories = (await session.execute(text("SELECT count(*) FROM categories"))).scalar()
                assert categories == 3  # A + B + system
                symbols = (await session.execute(text("SELECT count(*) FROM finance_symbols"))).scalar()
                assert symbols == 2
                # service sessions may also write system-tenant rows
                await session.execute(
                    text(
                        "INSERT INTO categories (id, tenant_id, name, slug, type) "
                        "VALUES (gen_random_uuid(), :tenant_id, 'service row', 'rls-it-service-write', 'news')"
                    ),
                    {"tenant_id": SYSTEM_TENANT},
                )
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_quote_partition_direct_access_enforced(rls_db) -> None:
    """Direct partition access is fenced too: partitions carry their own
    ENABLE/FORCE/policy (they do not inherit the parent's flags)."""
    from app.db.rls import bind_tenant_context

    partition = quote_partition_name(datetime.now(UTC).year, datetime.now(UTC).month)

    async def _run() -> None:
        engine, factory = _probe_session(rls_db)
        try:
            async with factory() as session:
                bind_tenant_context(session, str(TENANT_A))
                # own-tenant insert routed via the parent: accepted
                await session.execute(
                    text(
                        f"INSERT INTO {partition} (id, tenant_id, symbol_id, current_price, timestamp) "
                        "VALUES (gen_random_uuid(), :tenant_id, :symbol_id, 1.0, now())"
                    ),
                    {"tenant_id": TENANT_A, "symbol_id": SYM_A},
                )
                # cross-tenant insert straight into the partition: rejected
                with pytest.raises(Exception) as excinfo:
                    async with session.begin_nested():
                        await session.execute(
                            text(
                                f"INSERT INTO {partition} (id, tenant_id, symbol_id, current_price, timestamp) "
                                "VALUES (gen_random_uuid(), :tenant_id, :symbol_id, 1.0, now())"
                            ),
                            {"tenant_id": TENANT_B, "symbol_id": SYM_B},
                        )
                _assert_rls_violation(excinfo.value, partition)
                # direct reads are scoped as well
                visible = (
                    (
                        await session.execute(
                            text(f"SELECT tenant_id FROM {partition}"),
                        )
                    )
                    .scalars()
                    .all()
                )
                assert set(visible) == {TENANT_A}, visible
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def test_transaction_local_guc_does_not_leak_between_sessions(rls_db) -> None:
    """A GUC applied for one session must not leak into the next session that
    happens to reuse the same pooled connection (is_local=TRUE semantics)."""
    from app.db.rls import bind_tenant_context

    async def _run() -> None:
        # A real pool with a single connection on purpose: the second session
        # reuses the pooled connection the first one released. (NullPool would
        # physically close the connection and cannot demonstrate reuse.)
        from sqlalchemy.pool import AsyncAdaptedQueuePool

        engine, factory = _probe_session(rls_db, poolclass=AsyncAdaptedQueuePool, pool_size=1, max_overflow=0)
        try:
            async with factory() as session:
                bind_tenant_context(session, str(TENANT_A))
                count = (await session.execute(text("SELECT count(*) FROM categories"))).scalar()
                assert count == 2
                await session.commit()

            async with factory() as session:
                count = (await session.execute(text("SELECT count(*) FROM categories"))).scalar()
                assert count == 0, "tenant GUC leaked into the next session"
                await session.rollback()
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# background write path smoke test
# ---------------------------------------------------------------------------


def test_run_collection_writes_on_rls_enabled_database(rls_db, monkeypatch) -> None:
    """The real ``_run_collection`` path still stores items with RLS forced.

    The app role owns the tables, so FORCE ROW LEVEL SECURITY subjects it to
    the policy: this test only passes if the session opened inside
    ``_run_collection`` carries the ``app.is_service`` bypass (the wiring this
    feature adds). A control insert without the service context must fail.
    """
    from unittest.mock import AsyncMock, patch

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.collectors.base import CollectionResult
    from app.processors.base import ProcessorChain
    from app.scheduler.manager import scheduler_manager

    # Connect as the table OWNER (non-superuser). A superuser connection
    # would bypass RLS outright and let the control insert pass for the wrong
    # reason; the owner role is exactly who FORCE ROW LEVEL SECURITY fences.
    factory = async_sessionmaker(
        create_async_engine(rls_db["owner_url"], poolclass=NullPool),
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def _fake_collect(source):
        return CollectionResult(
            items=[
                {
                    "title": "RLS smoke item",
                    "summary": "written through _run_collection under RLS",
                    "url": f"https://example.com/rls-smoke-{uuid.uuid4().hex}",
                    "published_at": datetime.now(UTC).isoformat(),
                    "priority": 5,
                }
            ],
            success=True,
            response_time_ms=3,
            source_id=str(source.id),
        )

    class _FakeCollector:
        async def collect(self, source):
            return await _fake_collect(source)

    async def _run() -> None:
        # Control: the same insert WITHOUT the service context must be
        # rejected — proof that RLS is genuinely in force for the owner role.
        from app.models.item import Item

        async with factory() as session:
            with pytest.raises(Exception) as excinfo:
                async with session.begin():
                    session.add(
                        Item(
                            tenant_id=TENANT_A,
                            category_id=CAT_A,
                            source_id=SRC_A,
                            title="control insert without service context",
                            url=f"https://example.com/rls-control-{uuid.uuid4().hex}",
                            published_at=datetime.now(UTC),
                        )
                    )
            _assert_rls_violation(excinfo.value, "items")

        # The real background path: collector + processor chain + SSE push are
        # stubbed (no network / Redis), everything else is the production code.
        monkeypatch.setattr("app.db.session.async_session_factory", factory)
        with (
            patch("app.collectors.resolve_collector", return_value=_FakeCollector),
            patch("app.processors.create_default_processor_chain", return_value=ProcessorChain()),
            patch("app.services.sse.SSEService.publish_item_update", new=AsyncMock()),
            patch("app.services.sse.SSEService.publish_source_health_update", new=AsyncMock()),
        ):
            await scheduler_manager._run_collection(str(SRC_A))

        result = scheduler_manager._last_run_results.get(f"collect_{SRC_A}")
        assert result is not None, "collection job did not record a result"
        assert result.get("success") is True, result
        assert result.get("items_count") == 1, result

        # Verify through the superuser connection: an owner session without a
        # GUC is correctly blind to the stored row (default deny), which is
        # exactly what this feature guarantees.
        verify_engine = create_async_engine(rls_db["url"], poolclass=NullPool)
        try:
            async with verify_engine.connect() as conn:
                stored = (
                    await conn.execute(text("SELECT tenant_id, title FROM items WHERE title = 'RLS smoke item'"))
                ).all()
        finally:
            await verify_engine.dispose()
        assert len(stored) == 1, "collection item was not persisted under RLS"
        assert stored[0].tenant_id == TENANT_A

    asyncio.run(_run())


# Defined last: the downgrade tears down the RLS setup asserted above.
# Downgrades all the way to base (every migration's downgrade() in reverse):
# "-1" used to suffice while the tenancy RLS migration was the head, but once
# later migrations contribute their own RLS enablements (e.g. the fund intraday
# NAV tables in c3f2a8d1e9b4), a single step would leave the core tables'
# policies behind. Tearing down to base exercises every downgrade() and must
# leave zero tenant_isolation policies and zero RLS flags.
def test_downgrade_removes_rls(rls_db) -> None:
    command.downgrade(rls_db["cfg"], "base")

    async def _run() -> None:
        engine = create_async_engine(rls_db["url"], poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                policies = (
                    await conn.execute(
                        text("SELECT count(*) FROM pg_policy WHERE polname = :policy"),
                        {"policy": TENANT_ISOLATION_POLICY},
                    )
                ).scalar()
                assert policies == 0
                forced = (
                    await conn.execute(
                        text("SELECT count(*) FROM pg_class WHERE relrowsecurity OR relforcerowsecurity")
                    )
                ).scalar()
                assert forced == 0
        finally:
            await engine.dispose()

    asyncio.run(_run())
