#!/bin/bash
set -e

echo "=== Seeding InstantBoard database ==="

cd docker

echo "Running seed data via API container..."
docker compose exec api python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())" || {
    echo "Container not running, trying locally..."
    cd ../backend
    python -c "from app.db.init_db import init_db; import asyncio; asyncio.run(init_db())"
}

echo "=== Seed data complete ==="
