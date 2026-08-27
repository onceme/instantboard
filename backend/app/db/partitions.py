"""Monthly RANGE partition supply for finance_quotes (database.md §3.1).

finance_quotes is declared ``postgresql_partition_by = "RANGE (timestamp)"``
(app/models/finance.py), so on PostgreSQL ``create_tables`` builds the parent
as a partitioned table and every INSERT only succeeds when a partition exists
that covers the row's timestamp. This module idempotently creates the
previous/current/next month partitions:

- right after table creation (``db/init_db.py::create_tables`` calls
  ``ensure_quote_partitions``), which covers every startup path — the
  ``entrypoint.sh`` create-tables fallback, the ``main.py`` lifespan and the
  ``scheduler/worker.py`` main;
- daily at 00:30 UTC by the ``finance_quotes_partition_roll`` cron job
  (``scheduler/manager.py::add_quote_partition_job``) so the next month's
  partition always exists before the calendar rolls over.

Partition names follow ``finance_quotes_y{yyyy}m{mm}`` (zero-padded month)
and each covers the half-open UTC range [month start, next month start) —
the ``timestamp`` column is TIMESTAMPTZ and quotes are written with
``datetime.now(UTC)``.

Non-PostgreSQL dialects (SQLite in tests/dev) have no partitioning: the model
degrades to a plain table and every function here is a no-op.

Databases where finance_quotes already exists as a plain table are skipped
with a warning: ``create_all`` never alters existing tables, and turning a
plain table into a partitioned one requires the manual rebuild documented in
database.md §3.1.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger(__name__)

QUOTE_TABLE_NAME = "finance_quotes"
PARTITION_NAME_TEMPLATE = "finance_quotes_y{year:04d}m{month:02d}"

# PostgreSQL "duplicate_table" SQLSTATE, raised when the pre-check misses a
# partition created concurrently by another process between check and CREATE.
DUPLICATE_TABLE_SQLSTATE = "42P07"


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """Half-open UTC bounds ``[start, end)`` of one calendar month."""
    start = datetime(year, month, 1, tzinfo=UTC)
    next_month_index = year * 12 + month  # zero-based index of the next month
    next_year, next_month_zero = divmod(next_month_index, 12)
    end = datetime(next_year, next_month_zero + 1, 1, tzinfo=UTC)
    return start, end


def quote_partition_name(year: int, month: int) -> str:
    """Partition name for one month, ``finance_quotes_y{yyyy}m{mm}``."""
    return PARTITION_NAME_TEMPLATE.format(year=year, month=month)


def surrounding_month_partitions(moment: datetime | None = None) -> list[tuple[str, datetime, datetime]]:
    """``(name, from, to)`` for the previous, current and next month of ``moment``.

    Ordered chronologically; ``from``/``to`` are UTC-aware and contiguous
    (each month's ``to`` equals the next month's ``from``).
    """
    moment = moment or datetime.now(UTC)
    partitions: list[tuple[str, datetime, datetime]] = []
    for offset in (-1, 0, 1):
        month_index = moment.year * 12 + (moment.month - 1) + offset
        year, month_zero = divmod(month_index, 12)
        month = month_zero + 1
        start, end = month_bounds(year, month)
        partitions.append((quote_partition_name(year, month), start, end))
    return partitions


def _is_duplicate_table(exc: DBAPIError) -> bool:
    orig = exc.orig
    code = getattr(orig, "pgcode", None) or getattr(orig, "sqlstate", None)
    return code == DUPLICATE_TABLE_SQLSTATE


async def ensure_quote_partitions(engine: AsyncEngine | None = None) -> list[str]:
    """Idempotently create the prev/current/next month partitions of finance_quotes.

    PostgreSQL only — any other dialect returns ``[]`` without touching the
    database. Also returns ``[]`` (logged as a warning) when finance_quotes
    exists as a plain table: ``create_all`` never alters existing tables, so a
    pre-partitioning database needs the manual migration documented in
    database.md §3.1.

    Each partition is created in its own transaction: a concurrent duplicate
    (two processes starting up at once) is swallowed via the PG
    ``duplicate_table`` SQLSTATE instead of aborting the remaining creates.

    Returns the names of the partitions actually created by this call (empty
    when everything already existed). ``engine`` may be passed in to share a
    caller-owned engine (``create_tables`` does); otherwise a disposable
    engine is created from ``settings.database_url`` and disposed here.
    """
    own_engine = engine is None
    _engine = engine if engine is not None else create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        if _engine.dialect.name != "postgresql":
            logger.debug(f"finance_quotes partitioning skipped on dialect '{_engine.dialect.name}'")
            return []

        async with _engine.begin() as conn:
            # relkind is a PostgreSQL "char"; asyncpg decodes it as bytes, so
            # cast to text for a stable comparison.
            relkind = await conn.scalar(
                text("SELECT relkind::text FROM pg_class WHERE relname = :name"),
                {"name": QUOTE_TABLE_NAME},
            )
        if relkind is None:
            logger.debug(f"{QUOTE_TABLE_NAME} does not exist yet, skipping partition creation")
            return []
        if relkind != "p":
            logger.warning(
                f"{QUOTE_TABLE_NAME} exists but is not partitioned (relkind='{relkind}'); "
                "skipping partition creation — manual migration required (database.md §3.1)"
            )
            return []

        created: list[str] = []
        for partition_name, start, end in surrounding_month_partitions():
            async with _engine.begin() as conn:
                exists = await conn.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM pg_class WHERE relname = :name)"),
                    {"name": partition_name},
                )
                if exists:
                    continue
                try:
                    # asyncpg rejects bind parameters in DDL ("parameters are
                    # supported only in SELECT, INSERT, UPDATE, DELETE, MERGE
                    # and VALUES statements"), so the month bounds — values
                    # generated right here, never external input — are spelled
                    # as ISO-8601 literals.
                    await conn.execute(
                        text(
                            f"CREATE TABLE {partition_name} PARTITION OF {QUOTE_TABLE_NAME} "
                            f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                        )
                    )
                except DBAPIError as exc:
                    if not _is_duplicate_table(exc):
                        raise
                    logger.info(f"Partition {partition_name} was created concurrently, treating as existing")
                    continue
                created.append(partition_name)

        if created:
            logger.info(f"finance_quotes partitions created: {', '.join(created)}")
        else:
            logger.debug("finance_quotes partitions already present, nothing to create")
        return created
    finally:
        if own_engine:
            await _engine.dispose()
