-- InstantBoard PostgreSQL initialization script
-- Runs on first container startup

-- Set default timezone to UTC
SET timezone = 'UTC';

-- Create uuid-ossp extension for UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Create pgcrypto extension for hashing (used by RLS)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Grant permissions on the current database (determined by POSTGRES_DB env var).
-- The Docker entrypoint runs init scripts in the context of POSTGRES_DB,
-- so current_database() returns the correct name for both dev and prod.
DO $$
DECLARE
    db_name text;
BEGIN
    db_name := current_database();
    EXECUTE format('GRANT ALL PRIVILEGES ON DATABASE %I TO instantboard', db_name);
    RAISE NOTICE 'Granted all privileges on database % to instantboard', db_name;
END
$$;

-- Note: Row Level Security (RLS) policies will be created via Alembic migrations
-- after the application tables are created.
