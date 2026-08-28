import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import Text, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.sql.sqltypes import UUID, Uuid

from app.config import settings
from app.main import app
from app.models.base import Base


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(element, compiler, **kw):
    return "JSON"


def _uuid_sqlite_ddl(element, compiler, **kw):
    # Render UUID columns as CHAR(32) (TEXT affinity) on SQLite. The default "UUID"
    # declaration gets NUMERIC affinity, so the all-zero SYSTEM_TENANT_ID hex string
    # ("000...0") is coerced to integer 0 on write and breaks uuid.UUID() on read.
    return "CHAR(32)"


# Uuid and UUID have different __visit_name__ values, so register both.
compiles(Uuid, "sqlite")(_uuid_sqlite_ddl)
compiles(UUID, "sqlite")(_uuid_sqlite_ddl)


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
_engine_kwargs = (
    {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }
    if _is_sqlite
    else {
        "poolclass": NullPool,
    }
)

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
            await conn.run_sync(Base.metadata.drop_all)
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

    class MockPipeline:
        def __init__(self, redis):
            self._redis = redis
            self._commands = []

        def lpush(self, key, *values):
            self._commands.append(("lpush", key, values))
            return self

        def ltrim(self, key, start, end):
            self._commands.append(("ltrim", key, start, end))
            return self

        def expire(self, key, seconds):
            self._commands.append(("expire", key, seconds))
            return self

        async def execute(self):
            results = []
            for command in self._commands:
                name = command[0]
                if name == "lpush":
                    results.append(await self._redis.lpush(command[1], *command[2]))
                elif name == "ltrim":
                    results.append(await self._redis.ltrim(command[1], command[2], command[3]))
                elif name == "expire":
                    results.append(await self._redis.expire(command[1], command[2]))
            self._commands = []
            return results

    class MockRedis:
        def __init__(self):
            self._data = {}
            self._expiry = {}
            self._clock = 0.0

        def advance(self, seconds):
            """Fast-forward the virtual clock so TTL-marked keys expire deterministically."""
            self._clock += seconds

        def _purge_expired(self, key):
            expires_at = self._expiry.get(key)
            if expires_at is not None and self._clock >= expires_at:
                self._data.pop(key, None)
                self._expiry.pop(key, None)

        async def ping(self):
            return True

        async def get(self, key):
            self._purge_expired(key)
            return self._data.get(key)

        async def set(self, key, value, ex=None):
            self._data[key] = value
            if ex is not None:
                self._expiry[key] = self._clock + ex
            else:
                self._expiry.pop(key, None)

        async def delete(self, key):
            self._data.pop(key, None)
            self._expiry.pop(key, None)

        async def exists(self, key):
            self._purge_expired(key)
            return int(key in self._data)

        async def incr(self, key):
            self._purge_expired(key)
            value = int(self._data.get(key, 0)) + 1
            self._data[key] = value
            return value

        async def expire(self, key, seconds):
            if key not in self._data:
                return False
            self._expiry[key] = self._clock + seconds
            return True

        async def publish(self, channel, message):
            pass

        async def sadd(self, key, *members):
            existing = self._data.get(key)
            if not isinstance(existing, set):
                existing = set(existing) if existing else set()
                self._data[key] = existing
            before = len(existing)
            existing.update(members)
            return len(existing) - before

        async def srem(self, key, *members):
            existing = self._data.get(key)
            if not isinstance(existing, set):
                return 0
            before = len(existing)
            existing.difference_update(members)
            return before - len(existing)

        async def smembers(self, key):
            existing = self._data.get(key)
            return set(existing) if isinstance(existing, set) else set()

        async def sismember(self, key, member):
            return key in self._data and member in self._data[key]

        async def hset(self, key, mapping=None):
            self._data[key] = mapping

        async def hgetall(self, key):
            return self._data.get(key, {})

        async def lpush(self, key, *values):
            lst = self._data.setdefault(key, [])
            lst[0:0] = list(values)
            return len(lst)

        async def ltrim(self, key, start, end):
            lst = self._data.get(key, [])
            self._data[key] = lst[start : end + 1] if end >= 0 else lst[start:]
            return True

        async def lrange(self, key, start, end):
            lst = self._data.get(key, [])
            return lst[start : end + 1] if end >= 0 else lst[start:]

        def pipeline(self, transaction=True):
            return MockPipeline(self)

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
