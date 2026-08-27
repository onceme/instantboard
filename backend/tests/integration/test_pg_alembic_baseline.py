"""Real-PostgreSQL verification of the Alembic baseline migration
(database.md §3.4).

What the unit tests (static file checks) cannot prove, against a real
PostgreSQL:

- ``alembic upgrade head`` applies the baseline to an empty database and all
  12 model tables exist afterwards, with ``alembic_version`` stamped at head
- finance_quotes comes out as a partitioned parent (relkind 'p') with the
  composite PK (id, timestamp) — inserts still fail until partitions exist
- ``ensure_quote_partitions`` idempotently supplies the month partitions on
  the migration path (first call creates prev/current/next, second is a no-op)
  and inserts are routed into the current month partition
- a second ``alembic upgrade head`` is a no-op (idempotent startup)
- ``alembic downgrade base`` drops everything again

The suite runs on its own throwaway database (created/dropped around the
tests), so it never touches the shared ``instantboard_test`` schema the rest
of the integration suite uses. Skipped when DATABASE_URL is not PostgreSQL.
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from app.config import settings
from app.db.partitions import ensure_quote_partitions, quote_partition_name, surrounding_month_partitions
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="Alembic baseline migration tests require PostgreSQL",
)

EXPECTED_TABLES = frozenset(
    {
        "tenants",
        "users",
        "categories",
        "sources",
        "source_health",
        "items",
        "finance_symbols",
        "fund_nav_estimates",
        "watchlist_items",
        "finance_quotes",
        "dashboard_snapshots",
        "sse_connections",
    }
)

EXPECTED_CHECK_CONSTRAINTS = (
    "chk_tenants_plan",
    "chk_users_sso_provider",
    "chk_users_role",
    "chk_categories_type",
    "chk_sources_type",
    "chk_source_health_status",
    "chk_finance_symbols_type",
)

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "app" / "alembic" / "alembic.ini"


def _with_database(url: str, database: str) -> str:
    # NOTE: str(URL) masks the password as '***'; render_as_string keeps it.
    return make_url(url).set(database=database).render_as_string(hide_password=False)


def _maintenance_engine():
    # CREATE/DROP DATABASE cannot run inside a transaction block.
    return create_async_engine(
        _with_database(TEST_DATABASE_URL, "postgres"),
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
    )


async def _scalar(url: str, sql: str, **params):
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            return await conn.scalar(text(sql), params)
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def migrated_db():
    """A throwaway database upgraded to head with month partitions supplied."""
    db_name = f"alembic_it_{uuid.uuid4().hex[:12]}"
    test_url = _with_database(TEST_DATABASE_URL, db_name)

    async def _create_database() -> None:
        engine = _maintenance_engine()
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f"CREATE DATABASE {db_name}"))
        finally:
            await engine.dispose()

    asyncio.run(_create_database())

    # env.py reads the database URL from app settings, not from the alembic
    # config file — point the singleton at the throwaway database for the
    # duration of the test module.
    original_url = settings.database_url
    settings.database_url = test_url

    cfg = AlembicConfig(str(ALEMBIC_INI))
    # Absolute script location so the test works regardless of the pytest cwd.
    cfg.set_main_option("script_location", str(ALEMBIC_INI.parent))
    cfg.set_main_option("sqlalchemy.url", test_url)

    head = ScriptDirectory.from_config(cfg).get_current_head()

    try:
        command.upgrade(cfg, "head")
        first_created = asyncio.run(ensure_quote_partitions())
        second_created = asyncio.run(ensure_quote_partitions())
        yield {
            "db_name": db_name,
            "url": test_url,
            "cfg": cfg,
            "head": head,
            "first_created": first_created,
            "second_created": second_created,
        }
    finally:
        settings.database_url = original_url

        async def _drop_database() -> None:
            engine = _maintenance_engine()
            try:
                async with engine.connect() as conn:
                    await conn.execute(text(f"DROP DATABASE IF EXISTS {db_name} WITH (FORCE)"))
            finally:
                await engine.dispose()

        asyncio.run(_drop_database())


def test_all_model_tables_created(migrated_db) -> None:
    async def _tables() -> set[str]:
        engine = create_async_engine(migrated_db["url"], poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text(
                        "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
                    )
                )
                return {name for (name,) in rows}
        finally:
            await engine.dispose()

    tables = asyncio.run(_tables())
    assert tables >= EXPECTED_TABLES, f"missing tables: {EXPECTED_TABLES - tables}"


def test_alembic_version_stamped_at_head(migrated_db) -> None:
    version_num = asyncio.run(_scalar(migrated_db["url"], "SELECT version_num FROM alembic_version"))
    assert version_num == migrated_db["head"]


def test_finance_quotes_is_partitioned_parent(migrated_db) -> None:
    relkind = asyncio.run(
        _scalar(migrated_db["url"], "SELECT relkind::text FROM pg_class WHERE relname = 'finance_quotes'")
    )
    assert relkind == "p"

    pk_columns = asyncio.run(
        _scalar(
            migrated_db["url"],
            "SELECT string_agg(a.attname, ',' ORDER BY a.attnum) FROM pg_index i "
            "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'finance_quotes'::regclass AND i.indisprimary",
        )
    )
    assert pk_columns == "id,timestamp"


def test_check_constraints_and_partial_index(migrated_db) -> None:
    placeholders = ", ".join(f":c{i}" for i in range(len(EXPECTED_CHECK_CONSTRAINTS)))
    params = {f"c{i}": name for i, name in enumerate(EXPECTED_CHECK_CONSTRAINTS)}
    check_count = asyncio.run(
        _scalar(
            migrated_db["url"],
            f"SELECT COUNT(*) FROM pg_constraint WHERE contype = 'c' AND conname IN ({placeholders})",
            **params,
        )
    )
    assert check_count == len(EXPECTED_CHECK_CONSTRAINTS)

    partial_index = asyncio.run(
        _scalar(
            migrated_db["url"],
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_sse_connections_active'",
        )
    )
    assert partial_index is not None
    assert "WHERE" in partial_index and "disconnected_at IS NULL" in partial_index


def test_partition_supply_on_migration_path(migrated_db) -> None:
    expected = {name for name, _, _ in surrounding_month_partitions()}
    assert set(migrated_db["first_created"]) == expected
    assert migrated_db["second_created"] == [], "second supply run must be a no-op"


def test_quote_insert_routed_to_current_partition(migrated_db) -> None:
    async def _insert() -> str:
        engine = create_async_engine(migrated_db["url"], poolclass=NullPool)
        try:
            async with engine.begin() as conn:
                slug = f"alb-{uuid.uuid4().hex[:8]}"
                tenant_id = await conn.scalar(
                    text("INSERT INTO tenants (name, slug) VALUES ('Alembic IT', :slug) RETURNING id"),
                    {"slug": slug},
                )
                symbol_id = await conn.scalar(
                    text(
                        "INSERT INTO finance_symbols (tenant_id, symbol, name, type, market) "
                        "VALUES (:tenant_id, :symbol, 'Alembic IT Index', 'index', 'GLOBAL') RETURNING id"
                    ),
                    {"tenant_id": tenant_id, "symbol": f"ALB{uuid.uuid4().hex[:5].upper()}"},
                )
                return await conn.scalar(
                    text(
                        "INSERT INTO finance_quotes (tenant_id, symbol_id, current_price, timestamp) "
                        "VALUES (:tenant_id, :symbol_id, 1.0, now()) RETURNING tableoid::regclass::text"
                    ),
                    {"tenant_id": tenant_id, "symbol_id": symbol_id},
                )
        finally:
            await engine.dispose()

    now = datetime.now(UTC)
    assert asyncio.run(_insert()) == quote_partition_name(now.year, now.month)


def test_second_upgrade_is_idempotent(migrated_db) -> None:
    command.upgrade(migrated_db["cfg"], "head")
    version_num = asyncio.run(_scalar(migrated_db["url"], "SELECT version_num FROM alembic_version"))
    assert version_num == migrated_db["head"]


# Defined last: downgrade destroys the migrated schema the tests above rely on.
def test_downgrade_base_drops_everything(migrated_db) -> None:
    command.downgrade(migrated_db["cfg"], "base")

    async def _remaining_tables() -> set[str]:
        engine = create_async_engine(migrated_db["url"], poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    text(
                        "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')"
                    )
                )
                return {name for (name,) in rows}
        finally:
            await engine.dispose()

    remaining = asyncio.run(_remaining_tables())
    assert not (EXPECTED_TABLES & remaining), f"tables left after downgrade: {EXPECTED_TABLES & remaining}"
    version_count = asyncio.run(_scalar(migrated_db["url"], "SELECT COUNT(*) FROM alembic_version"))
    assert version_count == 0
