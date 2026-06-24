#!/bin/bash
set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <migration_description>"
    echo "Example: $0 add_user_watchlist_table"
    exit 1
fi

MSG="$1"

echo "=== Generating Alembic migration: $MSG ==="

cd backend

if [ -f alembic.ini ]; then
    alembic revision --autogenerate -m "$MSG"
elif [ -f app/alembic/alembic.ini ]; then
    alembic -c app/alembic/alembic.ini revision --autogenerate -m "$MSG"
else
    echo "ERROR: No alembic.ini found. Make sure Alembic is configured."
    exit 1
fi

echo "=== Migration generated ==="
echo "Review the generated migration file, then run: make migrate"
