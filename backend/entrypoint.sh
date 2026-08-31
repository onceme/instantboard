#!/bin/bash
set -e

echo "=== InstantBoard Entry Point ==="

# --- Wait for PostgreSQL ---
echo "Waiting for PostgreSQL..."
PG_HOST="${PGHOST:-${DB_HOST:-postgres}}"
PG_PORT="${PGPORT:-5432}"
PG_USER="${PGUSER:-instantboard}"

until pg_isready -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" > /dev/null 2>&1; do
  echo "PostgreSQL not ready yet... waiting"
  sleep 1
done
echo "PostgreSQL is ready at $PG_HOST:$PG_PORT"

# --- Wait for Redis ---
echo "Waiting for Redis..."
REDIS_HOST="${REDISHOST:-${REDIS_HOST:-redis}}"
REDIS_PORT="${REDISPORT:-6379}"

if [ -n "$REDIS_PASSWORD" ]; then
  REDIS_AUTH_ARGS="-a $REDIS_PASSWORD"
else
  REDIS_AUTH_ARGS=""
fi

until redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" $REDIS_AUTH_ARGS ping > /dev/null 2>&1; do
  echo "Redis not ready yet... waiting"
  sleep 1
done
echo "Redis is ready at $REDIS_HOST:$REDIS_PORT"

# --- Run database migrations / create tables ---
echo "Setting up database schema..."
ALEMBIC_VERSIONS="app/alembic/versions"
ALEMBIC_HAS_MIGRATIONS=false

if [ -d "$ALEMBIC_VERSIONS" ] && [ "$(find "$ALEMBIC_VERSIONS" -maxdepth 1 -name '*.py' -not -name '__init__.py' 2>/dev/null | head -n 1)" ]; then
  ALEMBIC_HAS_MIGRATIONS=true
fi

if [ "$ALEMBIC_HAS_MIGRATIONS" = "true" ] && [ -f "app/alembic/alembic.ini" ]; then
  # --- Bootstrap for unversioned-but-schema-present databases ---
  # Some environments were first created with Base.metadata.create_all() before
  # the Alembic chain existed: every table is present but alembic_version was
  # never recorded. Running `upgrade head` from that state fails at the
  # baseline migration's CREATE TABLE (DuplicateTableError), so the migration
  # chain can never start and later migrations (e.g. the fund-NAV column
  # additions) never apply, leaving fund_nav_estimates without
  # holdings_coverage_percent / holdings_report_date and the fund-NAV endpoints
  # returning 500. Detect that state and stamp the chain up to c3f2a8d1e9b4
  # (the fund-NAV migration), so the subsequent `upgrade head` then runs only
  # the idempotent repair migration e5a9c4f7b2d1 which adds the missing
  # columns. See docs/dev-guide/design/fund-intraday-nav.md §3. This is a
  # one-time bootstrap: once alembic_version has a row, it is a no-op.
  # Best-effort (|| true) so a transient failure — most notably two containers
  # racing the one-time stamp — cannot crash container startup; the losing side
  # simply proceeds to `upgrade head`, which succeeds once the peer has stamped.
  python - <<'PYEOF' || true
import asyncio
import subprocess
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def detect() -> tuple[bool, bool, bool]:
    from app.config import settings

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            ver_exists = (
                await conn.execute(text("SELECT to_regclass('alembic_version')"))
            ).scalar() is not None
            has_rows = False
            if ver_exists:
                has_rows = (
                    (await conn.execute(text("SELECT count(*) FROM alembic_version"))).scalar() or 0
                ) > 0
            tenants_exist = (
                await conn.execute(text("SELECT to_regclass('tenants')"))
            ).scalar() is not None
    finally:
        await engine.dispose()
    return ver_exists, has_rows, tenants_exist


ver_exists, has_rows, tenants_exist = asyncio.run(detect())

if ver_exists and has_rows:
    print("Alembic version already recorded; skipping bootstrap stamp")
    sys.exit(0)
if not tenants_exist:
    print("No existing schema detected; migrations will run from baseline")
    sys.exit(0)

print(
    "Unversioned database with an existing schema detected; stamping up to "
    "c3f2a8d1e9b4 so the repair migration can supply the missing columns"
)
# Run the stamp through the alembic CLI in a subprocess: env.py calls
# asyncio.run() internally, which cannot be called from the event loop we are
# currently inside, so invoking the CLI (its own process) is required.
# `sys.executable -m alembic` is used (rather than the bare `alembic` binary)
# so it resolves regardless of whether alembic is on PATH.
subprocess.run(
    [sys.executable, "-m", "alembic", "-c", "app/alembic/alembic.ini", "stamp", "c3f2a8d1e9b4"],
    check=True,
)
print("Stamped alembic_version to c3f2a8d1e9b4")
PYEOF
  echo "Running Alembic migrations..."
  alembic -c app/alembic/alembic.ini upgrade head || {
    echo "Alembic migration failed, falling back to create_all..."
    python -c "from app.db.init_db import create_tables; import asyncio; asyncio.run(create_tables())"
  }
  # The baseline migration creates finance_quotes as a partitioned parent
  # (PARTITION BY RANGE (timestamp)) but never creates the month partitions:
  # inserts fail until they exist. ensure_quote_partitions is the same
  # idempotent PG-only supplier that create_tables calls on the fallback path
  # (app/db/partitions.py), so the migration path ends up with the identical
  # prev/current/next month partitions. Running it again is a no-op.
  echo "Supplying finance_quotes month partitions..."
  python -c "from app.db.partitions import ensure_quote_partitions; import asyncio; asyncio.run(ensure_quote_partitions())"
else
  echo "No Alembic migrations found (empty versions directory), creating tables via SQLAlchemy..."
  python -c "from app.db.init_db import create_tables; import asyncio; asyncio.run(create_tables())"
fi

# --- Initialize seed data ---
echo "Initializing seed data..."
python -c "from app.db.init_db import seed_default_data; import asyncio; asyncio.run(seed_default_data())" || true

# --- Run the provided command ---
echo "Starting application: $*"

if [ $# -gt 0 ]; then
  exec "$@"
else
  echo "No command specified. Please provide a command via CMD."
  exit 1
fi
