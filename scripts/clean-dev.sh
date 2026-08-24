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

echo "=== Cleaning InstantBoard development environment ==="

cd docker

echo "Stopping all containers..."
$DOCKER_COMPOSE down

echo "Removing all volumes and local images..."
$DOCKER_COMPOSE down -v --rmi local

echo "Removing node_modules..."
rm -rf ../frontend/node_modules

echo "Removing Python cache..."
find ../backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find ../backend -type f -name "*.pyc" -delete 2>/dev/null || true

echo "=== Clean complete ==="
echo "Run 'make dev' or 'scripts/setup-dev.sh' to restart."
