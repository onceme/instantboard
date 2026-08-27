"""Unit tests for finance_quotes monthly partition supply (database.md §3.1).

Covers the pure helpers (month bounds, partition naming, prev/current/next
selection) and ensure_quote_partitions behaviour against a mocked engine:
non-PostgreSQL skip, missing table, plain (unpartitioned) table, partition
creation, idempotent re-runs and the concurrent-duplicate (42P07) tolerance.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import DBAPIError

import app.db.partitions as partitions_mod
from app.db.partitions import (
    ensure_quote_partitions,
    month_bounds,
    quote_partition_name,
    surrounding_month_partitions,
)


def _make_pg_engine(scalar_results, execute_side_effect=None):
    """Engine mock whose begin() always yields the same mock connection."""
    conn = AsyncMock()
    conn.scalar = AsyncMock(side_effect=list(scalar_results))
    conn.execute = AsyncMock(side_effect=execute_side_effect)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    engine = MagicMock()
    engine.dialect.name = "postgresql"
    engine.begin = MagicMock(return_value=ctx)
    engine.dispose = AsyncMock()
    return engine, conn


def _dbapi_error(sqlstate):
    # spec=["sqlstate"]: a bare MagicMock would auto-create a truthy .pgcode
    # attribute and mask the sqlstate fallback (asyncpg exposes sqlstate).
    orig = MagicMock(spec=["sqlstate"])
    orig.sqlstate = sqlstate
    return DBAPIError("CREATE TABLE ...", {}, orig)


# ── Pure helpers ─────────────────────────────────────────────────
class TestMonthBounds:
    def test_regular_month(self):
        start, end = month_bounds(2026, 8)
        assert start == datetime(2026, 8, 1, tzinfo=UTC)
        assert end == datetime(2026, 9, 1, tzinfo=UTC)

    def test_december_rolls_into_next_year(self):
        start, end = month_bounds(2026, 12)
        assert start == datetime(2026, 12, 1, tzinfo=UTC)
        assert end == datetime(2027, 1, 1, tzinfo=UTC)

    def test_bounds_are_utc(self):
        start, end = month_bounds(2026, 2)
        assert start.tzinfo is UTC
        assert end.tzinfo is UTC


class TestQuotePartitionName:
    def test_zero_pads_month(self):
        assert quote_partition_name(2026, 8) == "finance_quotes_y2026m08"

    def test_two_digit_month(self):
        assert quote_partition_name(2026, 12) == "finance_quotes_y2026m12"

    def test_four_digit_year(self):
        assert quote_partition_name(999, 1) == "finance_quotes_y0999m01"


class TestSurroundingMonthPartitions:
    def test_mid_year_prev_current_next(self):
        partitions = surrounding_month_partitions(datetime(2026, 8, 15, tzinfo=UTC))
        assert [name for name, _, _ in partitions] == [
            "finance_quotes_y2026m07",
            "finance_quotes_y2026m08",
            "finance_quotes_y2026m09",
        ]

    def test_january_rolls_back_a_year(self):
        partitions = surrounding_month_partitions(datetime(2026, 1, 5, tzinfo=UTC))
        assert [name for name, _, _ in partitions] == [
            "finance_quotes_y2025m12",
            "finance_quotes_y2026m01",
            "finance_quotes_y2026m02",
        ]

    def test_december_rolls_forward_a_year(self):
        partitions = surrounding_month_partitions(datetime(2026, 12, 20, tzinfo=UTC))
        assert [name for name, _, _ in partitions] == [
            "finance_quotes_y2026m11",
            "finance_quotes_y2026m12",
            "finance_quotes_y2027m01",
        ]

    def test_bounds_match_calendar_months_and_are_contiguous(self):
        partitions = surrounding_month_partitions(datetime(2026, 8, 15, tzinfo=UTC))
        (prev_name, prev_from, prev_to), (cur_name, cur_from, cur_to), (next_name, next_from, next_to) = partitions
        assert (prev_from, prev_to) == month_bounds(2026, 7)
        assert (cur_from, cur_to) == month_bounds(2026, 8)
        assert (next_from, next_to) == month_bounds(2026, 9)
        assert prev_to == cur_from
        assert cur_to == next_from

    def test_defaults_to_now(self):
        captured = datetime.now(UTC)
        partitions = surrounding_month_partitions()
        assert len(partitions) == 3
        # The middle partition always covers the instant it was computed for.
        name, start, end = partitions[1]
        assert name == quote_partition_name(start.year, start.month)
        assert start <= captured < end or start <= datetime.now(UTC) < end


# ── ensure_quote_partitions dialect / table gating ───────────────
class TestEnsurePartitionsGating:
    async def test_non_postgres_dialect_is_a_noop(self):
        engine = MagicMock()
        engine.dialect.name = "sqlite"
        engine.begin = MagicMock()
        engine.dispose = AsyncMock()

        assert await ensure_quote_partitions(engine) == []
        engine.begin.assert_not_called()

    async def test_missing_table_is_a_noop(self):
        engine, conn = _make_pg_engine(scalar_results=[None])

        assert await ensure_quote_partitions(engine) == []
        conn.execute.assert_not_called()

    async def test_plain_table_is_skipped_with_warning(self, caplog):
        engine, conn = _make_pg_engine(scalar_results=["r"])

        with caplog.at_level("WARNING"):
            assert await ensure_quote_partitions(engine) == []
        conn.execute.assert_not_called()
        assert any("not partitioned" in record.message for record in caplog.records)
        assert any("database.md" in record.message for record in caplog.records)

    async def test_disposes_engine_it_created(self):
        engine, _ = _make_pg_engine(scalar_results=["p", True, True, True])
        with patch("app.db.partitions.create_async_engine", return_value=engine) as mock_create:
            await ensure_quote_partitions()  # engine=None → creates its own
        mock_create.assert_called_once()
        engine.dispose.assert_awaited_once()

    async def test_does_not_dispose_caller_owned_engine(self):
        engine, _ = _make_pg_engine(scalar_results=["p", True, True, True])
        await ensure_quote_partitions(engine)
        engine.dispose.assert_not_awaited()


# ── ensure_quote_partitions creation paths ───────────────────────
class TestEnsurePartitionsCreation:
    async def test_creates_three_surrounding_month_partitions(self):
        # relkind check + one EXISTS check per month (all missing).
        engine, conn = _make_pg_engine(scalar_results=["p", False, False, False])

        created = await ensure_quote_partitions(engine)

        expected = [name for name, _, _ in surrounding_month_partitions()]
        assert created == expected
        assert conn.execute.await_count == 3
        for call, (name, start, end) in zip(conn.execute.await_args_list, surrounding_month_partitions(), strict=True):
            sql = str(call.args[0].text)
            # asyncpg rejects bind parameters in DDL: the bounds are rendered
            # as ISO-8601 literals generated from the month helpers.
            assert f"CREATE TABLE {name} PARTITION OF finance_quotes" in sql
            assert f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')" in sql
            assert len(call.args) == 1  # no params dict

    async def test_idempotent_when_all_partitions_exist(self):
        engine, conn = _make_pg_engine(scalar_results=["p", True, True, True])

        assert await ensure_quote_partitions(engine) == []
        conn.execute.assert_not_called()

    async def test_creates_only_missing_months(self):
        engine, conn = _make_pg_engine(scalar_results=["p", True, False, True])

        created = await ensure_quote_partitions(engine)

        assert created == [surrounding_month_partitions()[1][0]]
        conn.execute.assert_awaited_once()

    async def test_concurrent_duplicate_is_swallowed(self):
        engine, conn = _make_pg_engine(
            scalar_results=["p", False, False, False],
            execute_side_effect=_dbapi_error("42P07"),
        )

        # Another process won the race for every partition: no error, nothing
        # reported as created by us.
        assert await ensure_quote_partitions(engine) == []

    async def test_duplicate_detected_via_pgcode_attribute(self):
        orig = MagicMock(spec=[])  # no sqlstate attribute
        orig.pgcode = "42P07"
        exc = DBAPIError("CREATE TABLE ...", {}, orig)
        engine, conn = _make_pg_engine(
            scalar_results=["p", False, True, True],
            execute_side_effect=exc,
        )

        assert await ensure_quote_partitions(engine) == []

    async def test_other_sqlstate_propagates(self, monkeypatch):
        engine, conn = _make_pg_engine(
            scalar_results=["p", False, False, False],
            execute_side_effect=_dbapi_error("22023"),
        )

        with pytest.raises(DBAPIError):
            await ensure_quote_partitions(engine)

    async def test_concurrent_duplicate_logs_info(self, caplog):
        engine, _ = _make_pg_engine(
            scalar_results=["p", False, True, True],
            execute_side_effect=_dbapi_error("42P07"),
        )

        with caplog.at_level("INFO"):
            await ensure_quote_partitions(engine)
        assert any("concurrently" in record.message for record in caplog.records)


# ── create_tables wiring ─────────────────────────────────────────
class TestCreateTablesWiring:
    async def test_create_tables_passes_its_engine_to_ensure(self):
        from app.db.init_db import create_tables

        mock_conn = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=mock_conn)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=ctx)
        mock_engine.dispose = AsyncMock()

        with (
            patch("app.db.init_db.create_async_engine", return_value=mock_engine),
            patch("app.db.init_db.ensure_quote_partitions", new_callable=AsyncMock) as mock_ensure,
        ):
            await create_tables()

        # Partitions are supplied on the same engine, right after create_all —
        # this covers entrypoint.sh / main.py lifespan / worker startup alike.
        mock_ensure.assert_awaited_once_with(mock_engine)
        mock_engine.dispose.assert_awaited_once()
