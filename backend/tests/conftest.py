import asyncio
import os
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy import Text, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.main import app
from app.models.base import Base
from app.config import settings


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


class _PgDefaultSentinel:
    def __init__(self, original):
        self.original = original


def _strip_pg_server_defaults():
    stripped: list[tuple] = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if col.server_default is not None:
                arg = getattr(col.server_default, "arg", None)
                if arg is not None:
                    sql = str(arg) if hasattr(arg, "text") else str(arg)
                    if "::jsonb" in sql or "gen_random_uuid" in sql or "NOW()" in sql:
                        stripped.append((col, col.server_default))
                        col.server_default = None
    return stripped


def _restore_server_defaults(stripped: list[tuple]) -> None:
    for col, sd in stripped:
        col.server_default = sd


TEST_DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./test.db")

_is_sqlite = TEST_DATABASE_URL.startswith("sqlite")
_engine_kwargs = {
    "connect_args": {"check_same_thread": False},
    "poolclass": StaticPool,
} if _is_sqlite else {}

test_engine = create_async_engine(
    TEST_DATABASE_URL,
    **_engine_kwargs,
)

test_session_factory = async_sessionmaker(
    test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_database():
    stripped = _strip_pg_server_defaults() if _is_sqlite else []
    try:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        if stripped:
            _restore_server_defaults(stripped)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def redis_mock():
    """Mock Redis client for tests."""
    class MockRedis:
        def __init__(self):
            self._data = {}

        async def ping(self):
            return True

        async def get(self, key):
            return self._data.get(key)

        async def set(self, key, value, ex=None):
            self._data[key] = value

        async def delete(self, key):
            self._data.pop(key, None)

        async def publish(self, channel, message):
            pass

        async def sadd(self, key, *members):
            return len(members)

        async def sismember(self, key, member):
            return key in self._data and member in self._data[key]

        async def hset(self, key, mapping=None):
            self._data[key] = mapping

        async def hgetall(self, key):
            return self._data.get(key, {})

        async def close(self):
            pass

        async def aclose(self):
            pass

    return MockRedis()


@pytest.fixture
def client():
    """FastAPI TestClient for synchronous tests."""
    return TestClient(app)


@pytest_asyncio.fixture
async def async_client():
    """Async HTTP client for async tests."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
