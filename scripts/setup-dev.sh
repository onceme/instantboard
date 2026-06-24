#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "=== InstantBoard Development Environment Setup ==="
echo "Project root: $PROJECT_ROOT"

echo "Checking prerequisites..."

command -v docker >/dev/null 2>&1 || { echo "ERROR: Docker is not installed. Please install Docker Engine 24+."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: Docker Compose V2 is not installed."; exit 1; }
command -v git >/dev/null 2>&1 || { echo "ERROR: Git is not installed."; exit 1; }

echo "Prerequisites OK."

if [ ! -f "$PROJECT_ROOT/.env" ]; then
    echo "Creating .env from .env.example..."
    cp "$PROJECT_ROOT/.env.example" "$PROJECT_ROOT/.env"
    echo "Created .env. Please review and update API keys as needed."
else
    echo ".env already exists, skipping."
fi

echo "Building and starting development environment..."
cd "$PROJECT_ROOT/docker" && docker compose up -d --build

echo ""
echo "=== Setup Complete ==="
echo "API:        http://localhost:8000"
echo "API Docs:   http://localhost:8000/docs"
echo "Frontend:   http://localhost:3000"
echo ""
echo "Useful commands:"
echo "  make logs       - View service logs"
echo "  make down       - Stop all services"
echo "  make help       - Show all available commands"
echo "  make logs-api   - View API logs only"
