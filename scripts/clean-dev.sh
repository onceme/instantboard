#!/bin/bash
set -e

echo "=== Cleaning InstantBoard development environment ==="

cd docker

echo "Stopping all containers..."
docker compose down

echo "Removing all volumes and local images..."
docker compose down -v --rmi local

echo "Removing node_modules..."
rm -rf ../frontend/node_modules

echo "Removing Python cache..."
find ../backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find ../backend -type f -name "*.pyc" -delete 2>/dev/null || true

echo "=== Clean complete ==="
echo "Run 'make dev' or 'scripts/setup-dev.sh' to restart."
