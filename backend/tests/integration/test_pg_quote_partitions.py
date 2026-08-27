"""Real-PostgreSQL verification of finance_quotes monthly range partitioning
(database.md §3.1).

What the unit tests cannot prove, against a real PostgreSQL:

- create_all renders finance_quotes as a partitioned parent (relkind 'p')
  with the partition key on timestamp
- ensure_quote_partitions creates the prev/current/next month partitions with
  the correct FOR VALUES bounds, attached to the parent
- inserts are routed to the expected partition, including rows exactly on the
  UTC month boundaries (from inclusive, to exclusive)
- a second ensure_quote_partitions run is a no-op (idempotency)
- a plain (unpartitioned) table is skipped without error
- the concurrent-duplicate detection matches real asyncpg errors (42P07)

Skipped when DATABASE_URL is not PostgreSQL. Partition DDL runs on dedicated
connections, so the partitions it creates persist beyond the rolled-back
db_session transactions; the session-scoped teardown drop_all removes the
parent together with all partitions.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.partitions import (
    _is_duplicate_table,
    ensure_quote_partitions,
    quote_partition_name,
    surrounding_month_partitions,
)
from app.models.finance import FinanceQuote, FinanceSymbol
from app.models.tenant import Tenant
from tests.conftest import TEST_DATABASE_URL, test_engine

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="finance_quotes partitioning is PostgreSQL-only",
)


async def _drop_quote_partitions() -> list[str]:
    dropped = []
    async with test_engine.begin() as conn:
        rows = await conn.execute(
            text(
                "SELECT c.relname FROM pg_inherits i "
                "JOIN pg_class c ON c.oid = i.inhrelid "
                "JOIN pg_class p ON p.oid = i.inhparent "
                "WHERE p.relname = 'finance_quotes'"
            )
        )
        for (name,) in rows:
            await conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
            dropped.append(name)
    return dropped


@pytest.fixture
async def quote_env(db_session):
    """A fresh tenant + symbol for inserts (everything rolls back)."""
    tenant = Tenant(name="Partition Probe", slug=f"pq-{uuid.uuid4().hex[:8]}")
    db_session.add(tenant)
    await db_session.flush()
    symbol = FinanceSymbol(
        tenant_id=tenant.id,
        symbol=f"PQ{uuid.uuid4().hex[:6].upper()}",
        name="Partition Probe Symbol",
        type="stock",
        market="US",
        is_active=True,
    )
    db_session.add(symbol)
    await db_session.flush()
    return {"session": db_session, "tenant": tenant, "symbol": symbol}


def _make_quote(tenant, symbol, ts):
    return FinanceQuote(
        tenant_id=tenant.id,
        symbol_id=symbol.id,
        current_price=100,
        timestamp=ts,
        source_name="partition-test",
    )


async def _partition_of(session, quote):
    return await session.scalar(
        text("SELECT tableoid::regclass::text FROM finance_quotes WHERE id = :id AND timestamp = :ts"),
        {"id": quote.id, "ts": quote.timestamp},
    )


class TestPartitionedParent:
    async def test_finance_quotes_is_range_partitioned_on_timestamp(self):
        async with test_engine.connect() as conn:
            # relkind::text — asyncpg decodes the "char" type as bytes
            relkind = await conn.scalar(text("SELECT relkind::text FROM pg_class WHERE relname = 'finance_quotes'"))
            partkey = await conn.scalar(text("SELECT pg_get_partkeydef('finance_quotes'::regclass)"))
        assert relkind == "p"
        assert partkey is not None
        assert "timestamp" in partkey.lower()


class TestEnsureQuotePartitions:
    async def test_creates_surrounding_month_partitions_with_bounds(self):
        await _drop_quote_partitions()

        created = await ensure_quote_partitions(test_engine)
        expected = surrounding_month_partitions()

        assert created == [name for name, _, _ in expected]

        async with test_engine.begin() as conn:
            for name, start, end in expected:
                # Attached to the finance_quotes parent, bounds recorded by PG.
                bound = await conn.scalar(
                    text(
                        "SELECT pg_get_expr(c.relpartbound, c.oid) FROM pg_class c "
                        "JOIN pg_inherits i ON i.inhrelid = c.oid "
                        "JOIN pg_class p ON p.oid = i.inhparent "
                        "WHERE c.relname = :name AND p.relname = 'finance_quotes'"
                    ),
                    {"name": name},
                )
                assert bound is not None, f"partition {name} not attached to finance_quotes"
                assert start.strftime("%Y-%m-%d %H:%M:%S") in bound
                assert end.strftime("%Y-%m-%d %H:%M:%S") in bound

    async def test_second_run_is_idempotent(self):
        await _drop_quote_partitions()
        first = await ensure_quote_partitions(test_engine)
        second = await ensure_quote_partitions(test_engine)
        assert len(first) == 3
        assert second == []

        async with test_engine.begin() as conn:
            count = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_inherits i "
                    "JOIN pg_class p ON p.oid = i.inhparent "
                    "WHERE p.relname = 'finance_quotes'"
                )
            )
        assert count == 3

    async def test_plain_table_is_skipped_without_error(self, monkeypatch):
        import app.db.partitions as partitions_mod

        probe = "pq_probe_plain"
        monkeypatch.setattr(partitions_mod, "QUOTE_TABLE_NAME", probe)

        async with test_engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {probe}"))
            await conn.execute(text(f"CREATE TABLE {probe} (id uuid, timestamp timestamptz NOT NULL)"))
        try:
            created = await ensure_quote_partitions(test_engine)
            assert created == []

            async with test_engine.connect() as conn:
                # relkind::text — asyncpg decodes the "char" type as bytes
                relkind = await conn.scalar(text("SELECT relkind::text FROM pg_class WHERE relname = :n"), {"n": probe})
                children = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent WHERE p.relname = :n"
                    ),
                    {"n": probe},
                )
            # Still a plain table, nothing attached, no exception raised.
            assert relkind == "r"
            assert children == 0
        finally:
            async with test_engine.begin() as conn:
                await conn.execute(text(f"DROP TABLE IF EXISTS {probe}"))

    async def test_missing_table_is_skipped_without_error(self, monkeypatch):
        import app.db.partitions as partitions_mod

        monkeypatch.setattr(partitions_mod, "QUOTE_TABLE_NAME", "pq_probe_missing")
        assert await ensure_quote_partitions(test_engine) == []

    async def test_duplicate_detection_matches_real_asyncpg_error(self):
        """The swallowed-race branch must recognise a real asyncpg 42P07 error,
        not just the mocked unit-test shape. DDL cannot take bind parameters
        under asyncpg, so the bounds are inlined as literals (as in the code)."""
        await ensure_quote_partitions(test_engine)
        name, start, end = surrounding_month_partitions()[1]

        with pytest.raises(DBAPIError) as excinfo:
            async with test_engine.begin() as conn:
                await conn.execute(
                    text(
                        f"CREATE TABLE {name} PARTITION OF finance_quotes "
                        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                    )
                )
        assert _is_duplicate_table(excinfo.value)


class TestInsertRouting:
    async def test_insert_lands_in_current_month_partition(self, quote_env):
        await ensure_quote_partitions(test_engine)
        session, tenant, symbol = quote_env["session"], quote_env["tenant"], quote_env["symbol"]

        now = datetime.now(UTC)
        quote = _make_quote(tenant, symbol, now)
        session.add(quote)
        await session.flush()

        assert await _partition_of(session, quote) == quote_partition_name(now.year, now.month)

    async def test_boundary_rows_route_to_expected_partitions(self, quote_env):
        await ensure_quote_partitions(test_engine)
        session, tenant, symbol = quote_env["session"], quote_env["tenant"], quote_env["symbol"]

        prev_name, _, _ = surrounding_month_partitions()[0]
        current_name, current_start, current_end = surrounding_month_partitions()[1]
        next_name, next_start, _ = surrounding_month_partitions()[2]

        cases = [
            # (timestamp, expected partition)
            (current_start - timedelta(microseconds=1), prev_name),  # last instant of prev month
            (current_start, current_name),  # from bound is inclusive
            (current_end - timedelta(microseconds=1), current_name),  # last instant of current month
            (current_end, next_name),  # to bound is exclusive → next month
            (next_start, next_name),
        ]
        for ts, expected_partition in cases:
            quote = _make_quote(tenant, symbol, ts)
            session.add(quote)
            await session.flush()
            assert await _partition_of(session, quote) == expected_partition, f"ts={ts} misrouted"

    async def test_insert_outside_covered_months_fails(self, quote_env):
        """No default partition: a row two months ahead has nowhere to land.
        This is the failure mode the daily partition-roll job prevents."""
        await ensure_quote_partitions(test_engine)
        session, tenant, symbol = quote_env["session"], quote_env["tenant"], quote_env["symbol"]

        far_future = datetime.now(UTC).replace(day=1) + timedelta(days=70)
        quote = _make_quote(tenant, symbol, far_future)
        session.add(quote)
        with pytest.raises(DBAPIError):
            await session.flush()
