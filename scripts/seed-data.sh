#!/bin/bash
set -e

# Auto-detect Docker Compose version
if docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
elif docker-compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
else
  echo "ERROR: Neither 'docker compose' nor 'docker-compose' is installed."
  exit 1
fi

echo "=== Seeding InstantBoard database ==="

cd docker

echo "Running seed data via API container..."
$DOCKER_COMPOSE exec api python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())" || {
    echo "Container not running, trying locally..."
    cd ../backend
    python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())"
}

echo "=== Seed data complete ==="
