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
  echo "Running Alembic migrations..."
  alembic -c app/alembic/alembic.ini upgrade head || {
    echo "Alembic migration failed, falling back to create_all..."
    python -c "from app.db.init_db import create_tables; import asyncio; asyncio.run(create_tables())"
  }
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
