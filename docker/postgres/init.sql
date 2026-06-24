-- InstantBoard PostgreSQL initialization script
-- Runs on first container startup

-- Set default timezone to UTC
SET timezone = 'UTC';

-- Create uuid-ossp extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create pgcrypto extension for hashing (used by RLS)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Ensure the database exists (should already be created by POSTGRES_DB env var)
-- This is a safety check for manual setups
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_database WHERE datname = 'instantboard_dev') THEN
        PERFORM dblink_exec('dbname=postgres', 'CREATE DATABASE instantboard_dev');
    END IF;
END
$$;

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE instantboard_dev TO instantboard;

-- Note: Row Level Security (RLS) policies will be created via Alembic migrations
-- after the application tables are created.
