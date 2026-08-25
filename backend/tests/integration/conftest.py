"""
Integration tests conftest.
Handles lifespan mocking, dependency overrides, and auth helpers.
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from app.core.security import create_access_token
from tests.conftest import test_engine, test_session_factory

_redis_mock_instance = None


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
        return len(members)

    async def sismember(self, key, member):
        return key in self._data and member in self._data.get(key, set())

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


@asynccontextmanager
async def _mock_lifespan(app):
    from datetime import UTC, datetime

    import app.main as main_mod

    main_mod._start_time = datetime.now(UTC)
    yield


@pytest.fixture(scope="module")
def app_with_overrides():
    """Create an app instance with overridden lifespan and dependencies."""
    global _redis_mock_instance
    from app.main import app

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _mock_lifespan

    mock_redis = MockRedis()
    _redis_mock_instance = mock_redis

    async def override_get_db_session_dep():
        async with test_session_factory() as session:
            yield session

    async def override_get_db():
        """Override get_db to commit changes so flush()ed data persists across requests."""
        async with test_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def override_get_redis_client():
        return mock_redis

    from app.core.redis import get_redis_client as core_get_redis_client
    from app.db.session import get_db_session as db_get_db_session
    from app.dependencies import get_db

    app.dependency_overrides[db_get_db_session] = override_get_db_session_dep
    app.dependency_overrides[core_get_redis_client] = override_get_redis_client
    app.dependency_overrides[get_db] = override_get_db

    import app.core.redis as redis_mod
    import app.core.security as security_mod

    original_redis_get = redis_mod.redis_get
    original_redis_set = redis_mod.redis_set
    original_redis_delete = redis_mod.redis_delete
    original_redis_publish = redis_mod.redis_publish
    original_redis_hset = redis_mod.redis_hset
    original_redis_hgetall = redis_mod.redis_hgetall
    original_redis_sadd = redis_mod.redis_sadd
    original_redis_sismember = redis_mod.redis_sismember
    original_redis_push_history = redis_mod.redis_push_history
    original_redis_lrange = redis_mod.redis_lrange
    original_get_redis_client = redis_mod.get_redis_client

    async def mock_redis_get(key):
        return mock_redis._data.get(key)

    async def mock_redis_set(key, value, ex=None):
        import json

        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        mock_redis._data[key] = value

    async def mock_redis_delete(key):
        mock_redis._data.pop(key, None)

    async def mock_redis_publish(channel, message):
        pass

    async def mock_redis_hset(key, mapping=None):
        mock_redis._data[key] = mapping

    async def mock_redis_hgetall(key):
        return mock_redis._data.get(key, {})

    async def mock_redis_sadd(key, *members):
        return len(members)

    async def mock_redis_sismember(key, member):
        return False

    async def mock_redis_push_history(key, value, max_len, ttl=None):
        import json

        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        lst = mock_redis._data.setdefault(key, [])
        lst.insert(0, value)
        del lst[max_len:]

    async def mock_redis_lrange(key, start=0, end=-1):
        lst = mock_redis._data.get(key, [])
        if end == -1:
            end = len(lst) - 1
        return lst[start : end + 1] if end >= start else []

    async def mock_get_redis_client():
        return mock_redis

    redis_mod.redis_get = mock_redis_get
    redis_mod.redis_set = mock_redis_set
    redis_mod.redis_delete = mock_redis_delete
    redis_mod.redis_publish = mock_redis_publish
    redis_mod.redis_hset = mock_redis_hset
    redis_mod.redis_hgetall = mock_redis_hgetall
    redis_mod.redis_sadd = mock_redis_sadd
    redis_mod.redis_sismember = mock_redis_sismember
    redis_mod.redis_push_history = mock_redis_push_history
    redis_mod.redis_lrange = mock_redis_lrange
    redis_mod.get_redis_client = mock_get_redis_client

    sec_original_get = getattr(security_mod, "redis_get", None)
    sec_original_set = getattr(security_mod, "redis_set", None)
    if sec_original_get is not None:
        security_mod.redis_get = mock_redis_get
    if sec_original_set is not None:
        security_mod.redis_set = mock_redis_set

    yield app, mock_redis

    redis_mod.redis_get = original_redis_get
    redis_mod.redis_set = original_redis_set
    redis_mod.redis_delete = original_redis_delete
    redis_mod.redis_publish = original_redis_publish
    redis_mod.redis_hset = original_redis_hset
    redis_mod.redis_hgetall = original_redis_hgetall
    redis_mod.redis_sadd = original_redis_sadd
    redis_mod.redis_sismember = original_redis_sismember
    redis_mod.redis_push_history = original_redis_push_history
    redis_mod.redis_lrange = original_redis_lrange
    redis_mod.get_redis_client = original_get_redis_client

    if sec_original_get is not None:
        security_mod.redis_get = sec_original_get
    if sec_original_set is not None:
        security_mod.redis_set = sec_original_set

    app.dependency_overrides.clear()
    app.router.lifespan_context = original_lifespan
    _redis_mock_instance = None


@pytest.fixture(scope="module")
def client(app_with_overrides):
    app, _ = app_with_overrides
    return TestClient(app)


def make_auth_header(role="admin", provider="github"):
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    token = create_access_token(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "provider": provider,
            "type": "access",
        }
    )
    return {"Authorization": f"Bearer {token}"}, tenant_id, user_id


def make_admin_headers():
    return make_auth_header(role="admin")


def make_user_headers():
    return make_auth_header(role="user")
