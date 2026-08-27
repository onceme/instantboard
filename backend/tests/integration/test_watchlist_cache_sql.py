"""Real-SQL verification of the watchlist read-through Redis cache.

test_api_finance.py mocks FinanceService wholesale, so the cache contract of
get_watchlist (database.md §3.2: read/write, TTL 600s, invalidated on every
mutation, corrupt entries self-heal, Redis down degrades to PG) is exercised
here against a real DB session with a dict-backed fake Redis patched over the
module-level redis helpers. The app_with_overrides fixture is deliberately
avoided because its mock redis_set drops the TTL argument.

Read-path tests run on SQLite and PostgreSQL (UUID objects are passed like
the other dual-backend integration tests). Mutation tests are PG-only: the
service contract binds str ids against UUID columns, which only asyncpg
coerces (same gate as test_pg_tech_system_tenant.py). Mutations commit,
which survives db_session's rollback; rows are isolated in a fresh tenant
per test and wiped by setup_database's drop_all.
"""

import json
import uuid

import pytest

from app.core.redis import RedisKeys
from app.models.finance import FinanceSymbol
from app.models.tenant import Tenant
from app.models.user import User
from app.models.watchlist import WatchlistItem
from app.services.finance import REDIS_TTL_WATCHLIST, FinanceService
from tests.conftest import TEST_DATABASE_URL


class FakeRedis:
    """Dict-backed stand-in recording the TTL handed to redis_set."""

    def __init__(self):
        self.store = {}
        self.ttls = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value
        if ex is not None:
            self.ttls[key] = ex
        else:
            self.ttls.pop(key, None)

    async def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)


@pytest.fixture
def fake_redis():
    return FakeRedis()


@pytest.fixture
def patched_redis(fake_redis):
    from unittest.mock import patch

    with (
        patch("app.services.finance.redis_get", new=fake_redis.get),
        patch("app.services.finance.redis_set", new=fake_redis.set),
        patch("app.services.finance.redis_delete", new=fake_redis.delete),
    ):
        yield fake_redis


@pytest.fixture
async def watchlist_env(db_session):
    """Seed a tenant + user + three symbols; two already on the watchlist."""
    suffix = uuid.uuid4().hex[:8]
    tenant = Tenant(name="Watchlist Cache Tenant", slug=f"wl-cache-{suffix}")
    db_session.add(tenant)
    await db_session.flush()

    user = User(
        tenant_id=tenant.id,
        email=f"wl-cache-{suffix}@example.com",
        name="Watchlist Cache User",
        sso_provider="github",
        sso_provider_id=uuid.uuid4().hex,
    )
    db_session.add(user)

    symbols = [
        FinanceSymbol(tenant_id=tenant.id, symbol="AAPL", name="Apple Inc", type="stock", market="US"),
        FinanceSymbol(tenant_id=tenant.id, symbol="GOOG", name="Alphabet Inc", type="stock", market="US"),
        FinanceSymbol(tenant_id=tenant.id, symbol="MSFT", name="Microsoft Corp", type="stock", market="US"),
    ]
    db_session.add_all(symbols)
    await db_session.flush()

    items = [
        WatchlistItem(tenant_id=tenant.id, user_id=user.id, symbol_id=symbols[0].id, display_order=0),
        WatchlistItem(tenant_id=tenant.id, user_id=user.id, symbol_id=symbols[1].id, display_order=1),
    ]
    db_session.add_all(items)
    await db_session.flush()

    return {
        "session": db_session,
        "tenant": tenant,
        "user": user,
        "symbols": symbols,
        "items": items,
    }


def _service(env):
    return FinanceService(env["session"], None)


def _key(env):
    return RedisKeys.watchlist_key(str(env["tenant"].id), str(env["user"].id))


class TestWatchlistCacheRead:
    async def test_miss_populates_cache_with_ttl(self, watchlist_env, patched_redis):
        service = _service(watchlist_env)

        result = await service.get_watchlist(watchlist_env["tenant"].id, watchlist_env["user"].id)

        key = _key(watchlist_env)
        assert key in patched_redis.store
        assert patched_redis.ttls[key] == REDIS_TTL_WATCHLIST == 600
        assert json.loads(patched_redis.store[key]) == result
        assert [row["symbol"] for row in result] == ["AAPL", "GOOG"]

    async def test_hit_served_from_cache_not_pg(self, watchlist_env, patched_redis):
        service = _service(watchlist_env)

        first = await service.get_watchlist(watchlist_env["tenant"].id, watchlist_env["user"].id)

        # Mutate the DB behind the cache's back: a hit must keep serving the
        # cached payload (stale until invalidation), proving no PG re-query.
        watchlist_env["items"][0].display_order = 99
        watchlist_env["items"][0].notes = "edited-behind-cache"
        await watchlist_env["session"].flush()

        second = await service.get_watchlist(watchlist_env["tenant"].id, watchlist_env["user"].id)
        assert second == first
        assert second[0]["display_order"] == 0
        assert second[0]["notes"] is None

    @pytest.mark.parametrize("dirty", ["{not-json", "42", '{"a": 1}'])
    async def test_dirty_cache_entry_deleted_and_rebuilt(self, watchlist_env, patched_redis, dirty):
        patched_redis.store[_key(watchlist_env)] = dirty
        service = _service(watchlist_env)

        result = await service.get_watchlist(watchlist_env["tenant"].id, watchlist_env["user"].id)

        assert [row["symbol"] for row in result] == ["AAPL", "GOOG"]
        # The corrupt entry was replaced by a clean rebuild with a fresh TTL.
        assert json.loads(patched_redis.store[_key(watchlist_env)]) == result
        assert patched_redis.ttls[_key(watchlist_env)] == REDIS_TTL_WATCHLIST

    async def test_redis_read_down_degrades_to_pg(self, watchlist_env):
        from unittest.mock import patch

        async def broken(*args, **kwargs):
            raise ConnectionError("redis down")

        with (
            patch("app.services.finance.redis_get", new=broken),
            patch("app.services.finance.redis_set", new=broken),
            patch("app.services.finance.redis_delete", new=broken),
        ):
            service = _service(watchlist_env)
            result = await service.get_watchlist(watchlist_env["tenant"].id, watchlist_env["user"].id)

        assert [row["symbol"] for row in result] == ["AAPL", "GOOG"]


@pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgresql"),
    reason="str ids bound against UUID columns require PostgreSQL coercion",
)
class TestWatchlistCacheMutationInvalidation:
    async def test_add_invalidates_cache(self, watchlist_env, patched_redis):
        service = _service(watchlist_env)
        tenant_id, user_id = str(watchlist_env["tenant"].id), str(watchlist_env["user"].id)
        await service.get_watchlist(tenant_id, user_id)
        assert _key(watchlist_env) in patched_redis.store

        await service.add_to_watchlist(tenant_id, user_id, {"symbol_id": str(watchlist_env["symbols"][2].id)})

        assert _key(watchlist_env) not in patched_redis.store
        result = await service.get_watchlist(tenant_id, user_id)
        assert [row["symbol"] for row in result] == ["AAPL", "GOOG", "MSFT"]

    async def test_remove_invalidates_cache(self, watchlist_env, patched_redis):
        service = _service(watchlist_env)
        tenant_id, user_id = str(watchlist_env["tenant"].id), str(watchlist_env["user"].id)
        await service.get_watchlist(tenant_id, user_id)
        assert _key(watchlist_env) in patched_redis.store

        await service.remove_from_watchlist(tenant_id, user_id, str(watchlist_env["items"][1].id))

        assert _key(watchlist_env) not in patched_redis.store
        result = await service.get_watchlist(tenant_id, user_id)
        assert [row["symbol"] for row in result] == ["AAPL"]

    async def test_reorder_invalidates_cache(self, watchlist_env, patched_redis):
        service = _service(watchlist_env)
        tenant_id, user_id = str(watchlist_env["tenant"].id), str(watchlist_env["user"].id)
        await service.get_watchlist(tenant_id, user_id)
        assert _key(watchlist_env) in patched_redis.store

        await service.reorder_watchlist(
            tenant_id,
            user_id,
            [
                {"item_id": str(watchlist_env["items"][0].id), "display_order": 1},
                {"item_id": str(watchlist_env["items"][1].id), "display_order": 0},
            ],
        )

        assert _key(watchlist_env) not in patched_redis.store
        result = await service.get_watchlist(tenant_id, user_id)
        assert [row["symbol"] for row in result] == ["GOOG", "AAPL"]

    async def test_threshold_patch_invalidates_cache(self, watchlist_env, patched_redis):
        service = _service(watchlist_env)
        tenant_id, user_id = str(watchlist_env["tenant"].id), str(watchlist_env["user"].id)
        await service.get_watchlist(tenant_id, user_id)
        assert _key(watchlist_env) in patched_redis.store

        await service.update_watchlist_alert_threshold(tenant_id, user_id, str(watchlist_env["items"][0].id), 2.5)

        assert _key(watchlist_env) not in patched_redis.store
        result = await service.get_watchlist(tenant_id, user_id)
        assert result[0]["alert_threshold_percent"] == 2.5

    async def test_redis_down_mutations_still_commit(self, watchlist_env):
        from unittest.mock import patch

        async def broken(*args, **kwargs):
            raise ConnectionError("redis down")

        with (
            patch("app.services.finance.redis_get", new=broken),
            patch("app.services.finance.redis_set", new=broken),
            patch("app.services.finance.redis_delete", new=broken),
        ):
            service = _service(watchlist_env)
            tenant_id, user_id = str(watchlist_env["tenant"].id), str(watchlist_env["user"].id)

            result = await service.get_watchlist(tenant_id, user_id)
            assert [row["symbol"] for row in result] == ["AAPL", "GOOG"]

            # The failed cache delete cannot fail the mutation after commit.
            await service.remove_from_watchlist(tenant_id, user_id, str(watchlist_env["items"][1].id))

            result = await service.get_watchlist(tenant_id, user_id)
            assert [row["symbol"] for row in result] == ["AAPL"]
