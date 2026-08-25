"""E2E tests conftest.

Reuses the integration fixtures (full FastAPI app + SQLite + MockRedis) and upgrades
the mock for real user journeys: real set semantics for the collector dedup path,
scan_iter for the logout session sweep, and a fallback for Item.fetched_at (the
PG-only NOW() server default is stripped for SQLite, but the collection pipeline
never sets fetched_at explicitly).
"""

import fnmatch
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.sql import sqltypes

from app.core.security import create_access_token
from app.models.item import Item
from app.models.tenant import Tenant
from app.models.user import User
from tests.conftest import test_session_factory
from tests.integration.conftest import app_with_overrides  # noqa: F401  (fixture re-export)

# Services bind the JWT's str tenant/user ids to UUID columns throughout
# (asyncpg accepts str parameters for uuid columns on PostgreSQL). The SQLite test
# engine uses the character-based Uuid bind processor, which calls value.hex and
# rejects str. Accept str too so the same service code runs end-to-end on SQLite.
_original_uuid_bind_processor = sqltypes.Uuid.bind_processor


def _uuid_bind_processor_accepting_str(self, dialect):
    process = _original_uuid_bind_processor(self, dialect)
    if process is None:
        return None

    def process_lenient(value):
        if value is not None and not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return process(value)

    return process_lenient


sqltypes.Uuid.bind_processor = _uuid_bind_processor_accepting_str


def _fill_item_fetched_at(mapper, connection, target):
    if target.fetched_at is None:
        target.fetched_at = datetime.now(UTC)


if not event.contains(Item, "before_insert", _fill_item_fetched_at):
    event.listen(Item, "before_insert", _fill_item_fetched_at)


@pytest.fixture(scope="module")
def e2e_env(app_with_overrides):
    app, mock_redis = app_with_overrides

    async def sadd(key, *members, ttl=None):
        store = mock_redis._data.get(key)
        if not isinstance(store, set):
            store = set()
        added = len(set(members) - store)
        store.update(members)
        mock_redis._data[key] = store
        return added

    async def sismember(key, member):
        store = mock_redis._data.get(key)
        return isinstance(store, set) and member in store

    async def scan_iter(match=None):
        for key in list(mock_redis._data):
            if match is None or fnmatch.fnmatch(str(key), match):
                yield key

    mock_redis.sadd = sadd
    mock_redis.sismember = sismember
    mock_redis.scan_iter = scan_iter

    return app, mock_redis


@pytest.fixture(autouse=True)
def _clean_redis_state(e2e_env):
    """Wipe cached/blacklist/dedup keys so scenarios start from a clean slate."""
    _, mock_redis = e2e_env
    mock_redis._data.clear()
    mock_redis._expiry.clear()


@pytest.fixture(autouse=True)
def _wire_mock_redis_dedup(e2e_env, monkeypatch):
    """Point the dedup processor at set-backed mock redis functions.

    DedupProcessor resolves redis_sadd/redis_sismember as module globals at call
    time; whatever it bound at import (the real helpers or the integration mock
    whose sadd takes no ttl) must not leak into e2e collection runs.
    """
    _, mock_redis = e2e_env
    import app.processors.dedup as dedup_mod

    async def sadd(key, *members, ttl=None):
        store = mock_redis._data.get(key)
        if not isinstance(store, set):
            store = set()
        added = len(set(members) - store)
        store.update(members)
        mock_redis._data[key] = store
        return added

    async def sismember(key, member):
        store = mock_redis._data.get(key)
        return isinstance(store, set) and member in store

    monkeypatch.setattr(dedup_mod, "redis_sadd", sadd)
    monkeypatch.setattr(dedup_mod, "redis_sismember", sismember)


@pytest_asyncio.fixture
async def aclient(e2e_env):
    app, _ = e2e_env
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest_asyncio.fixture
async def e2e_tenant_and_user():
    async with test_session_factory() as session:
        suffix = uuid.uuid4().hex[:10]
        tenant = Tenant(name=f"E2E Tenant {suffix[:6]}", slug=f"e2e-{suffix}", plan="free", settings={})
        session.add(tenant)
        await session.flush()
        user = User(
            tenant_id=tenant.id,
            email=f"e2e-{suffix[:8]}@example.com",
            name="E2E User",
            sso_provider="github",
            sso_provider_id=f"gh-{uuid.uuid4().hex}",
            role="member",
        )
        session.add(user)
        await session.commit()
        return {"tenant_id": str(tenant.id), "user_id": str(user.id)}


def make_e2e_token(tenant_id: str, user_id: str, role: str = "member", provider: str = "github") -> str:
    return create_access_token(
        {
            "sub": user_id,
            "tenant_id": tenant_id,
            "role": role,
            "provider": provider,
        }
    )


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
