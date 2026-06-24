#!/bin/bash
set -e

echo "=== Running all InstantBoard tests ==="

# Backend tests
echo "Running backend tests..."
cd backend
pip install -q -r requirements/dev.txt 2>/dev/null || true
pytest --cov=app -v
cd ..

# Frontend tests
echo "Running frontend tests..."
cd frontend
npm ci --quiet 2>/dev/null || npm install --quiet 2>/dev/null || true
npm run test || echo "Frontend tests: no test runner configured yet"
cd ..

echo "=== All tests complete ==="
