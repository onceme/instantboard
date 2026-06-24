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

until redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping > /dev/null 2>&1; do
  echo "Redis not ready yet... waiting"
  sleep 1
done
echo "Redis is ready at $REDIS_HOST:$REDIS_PORT"

# --- Run Alembic migrations ---
echo "Running Alembic migrations..."
if [ -f "app/alembic/alembic.ini" ]; then
  alembic -c app/alembic/alembic.ini upgrade head || \
    python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())"
else
  echo "No alembic.ini found, running init_db directly..."
  python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())"
fi

# --- Initialize seed data ---
echo "Initializing seed data..."
python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())" || true

# --- Determine startup command ---
echo "Starting application..."

if [ "${ENV}" = "production" ] || [ "${APP_ENV}" = "production" ]; then
  # Production: use gunicorn with uvicorn workers
  exec gunicorn app.main:app \
    --bind "${UVICORN_HOST:-0.0.0.0}:${UVICORN_PORT:-8000}" \
    --workers "${UVICORN_WORKERS:-4}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 120 \
    --graceful_timeout 30 \
    --access-logfile - \
    --error-logfile -
else
  # Development: use uvicorn with reload
  exec uvicorn app.main:app \
    --host "${UVICORN_HOST:-0.0.0.0}" \
    --port "${UVICORN_PORT:-8000}" \
    --reload
fi
