"""Integration tests for the generic sort_by/sort_order params (GET /categories,
GET /sources, GET /admin/tenants — see docs/dev-guide/design/api.md §3.1).

Rows are seeded directly through the ORM so names/created_at/priority values are
deterministic. Assertions always filter the responses down to the seeded IDs
because other suites (e.g. system-tenant seeds) leave rows that are visible to
every list endpoint.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.sql import sqltypes

from app.core.security import create_access_token
from app.models.category import Category
from app.models.source import Source
from app.models.tenant import Tenant
from tests.conftest import test_session_factory

# Services bind the JWT's str tenant id against UUID columns (asyncpg accepts str).
# The SQLite test engine's Uuid bind processor calls value.hex and rejects str; the
# same lenient binding the e2e suite installs (tests/e2e/conftest.py).
if not getattr(sqltypes.Uuid.bind_processor, "_ib_accepts_str", False):
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

    _uuid_bind_processor_accepting_str._ib_accepts_str = True  # type: ignore[attr-defined]
    sqltypes.Uuid.bind_processor = _uuid_bind_processor_accepting_str


def _token(tenant_id: str) -> str:
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "role": "admin",
            "provider": "github",
            "type": "access",
        }
    )


def _headers(tenant_id: str) -> dict:
    return {"Authorization": f"Bearer {_token(tenant_id)}"}


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {_token(str(uuid.uuid4()))}"}


async def _seed_sort_fixture() -> dict:
    """Seed one tenant with three categories and three sources.

    created_at is set against the name ordering (Charlie newest, Alpha oldest) so
    the default order and sort_by=created_at produce different sequences.
    """
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()

    async with test_session_factory() as session:
        session.add(Tenant(id=tenant_id, name="Sort Fixture Tenant", slug=f"sort-{tenant_id.hex[:10]}", plan="free"))

        categories = {}
        for idx, name in enumerate(["Alpha", "Bravo", "Charlie"]):
            category = Category(
                tenant_id=tenant_id,
                name=f"Sort {name}",
                slug=f"sort-{name.lower()}-{tenant_id.hex[:8]}",
                type="tech",
                refresh_interval_seconds=300,
                is_active=True,
                created_at=now - timedelta(hours=2 - idx),  # Alpha oldest ... Charlie newest
            )
            session.add(category)
            categories[name] = category

        # Flush so category ids exist before sources reference them.
        await session.flush()

        sources = {}
        # priority deliberately diverges from the name ordering
        for name, priority in [("Alpha", 5), ("Bravo", 1), ("Charlie", 9)]:
            source = Source(
                tenant_id=tenant_id,
                category_id=categories[name].id,
                name=f"Sort Src {name}",
                source_type="rss",
                url="https://example.com/feed",
                config={},
                refresh_interval_seconds=300,
                is_active=True,
                priority=priority,
            )
            session.add(source)
            sources[name] = source

        await session.commit()

        return {
            "tenant_id": str(tenant_id),
            "category_ids": {name: str(cat.id) for name, cat in categories.items()},
            "source_ids": {name: str(src.id) for name, src in sources.items()},
        }


def _names_for(data: list[dict], wanted_ids: set[str]) -> list[str]:
    """Return the names of the seeded rows in response order."""
    return [row["name"] for row in data if row["id"] in wanted_ids]


class TestCategorySorting:
    async def test_default_order_keeps_type_then_name(self, client):
        fixture = await _seed_sort_fixture()
        resp = client.get("/api/v1/categories?page_size=100", headers=_headers(fixture["tenant_id"]))
        assert resp.status_code == 200

        names = _names_for(resp.json()["data"], set(fixture["category_ids"].values()))
        # default: type asc, name asc (all seeded rows share type "tech")
        assert names == ["Sort Alpha", "Sort Bravo", "Sort Charlie"]

    async def test_sort_by_created_at_desc_is_default_sort_order(self, client):
        fixture = await _seed_sort_fixture()
        resp = client.get(
            "/api/v1/categories?sort_by=created_at&page_size=100",
            headers=_headers(fixture["tenant_id"]),
        )
        assert resp.status_code == 200

        names = _names_for(resp.json()["data"], set(fixture["category_ids"].values()))
        # Charlie was seeded as the newest row
        assert names == ["Sort Charlie", "Sort Bravo", "Sort Alpha"]

    async def test_sort_by_name_asc_and_desc_are_reversed(self, client):
        fixture = await _seed_sort_fixture()

        asc = client.get(
            "/api/v1/categories?sort_by=name&sort_order=asc&page_size=100",
            headers=_headers(fixture["tenant_id"]),
        )
        desc = client.get(
            "/api/v1/categories?sort_by=name&sort_order=desc&page_size=100",
            headers=_headers(fixture["tenant_id"]),
        )
        assert asc.status_code == 200
        assert desc.status_code == 200

        asc_names = _names_for(asc.json()["data"], set(fixture["category_ids"].values()))
        desc_names = _names_for(desc.json()["data"], set(fixture["category_ids"].values()))
        assert asc_names == ["Sort Alpha", "Sort Bravo", "Sort Charlie"]
        assert desc_names == list(reversed(asc_names))

    async def test_invalid_sort_by_returns_400(self, client):
        fixture = await _seed_sort_fixture()
        resp = client.get(
            "/api/v1/categories?sort_by=slug",
            headers=_headers(fixture["tenant_id"]),
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == "sort_by"


class TestSourceSorting:
    async def test_default_order_keeps_priority_then_name(self, client):
        fixture = await _seed_sort_fixture()
        resp = client.get("/api/v1/sources?page_size=100", headers=_headers(fixture["tenant_id"]))
        assert resp.status_code == 200

        names = _names_for(resp.json()["data"], set(fixture["source_ids"].values()))
        # default: priority asc (Bravo 1, Alpha 5, Charlie 9)
        assert names == ["Sort Src Bravo", "Sort Src Alpha", "Sort Src Charlie"]

    async def test_sort_by_priority_desc(self, client):
        fixture = await _seed_sort_fixture()
        resp = client.get(
            "/api/v1/sources?sort_by=priority&sort_order=desc&page_size=100",
            headers=_headers(fixture["tenant_id"]),
        )
        assert resp.status_code == 200

        names = _names_for(resp.json()["data"], set(fixture["source_ids"].values()))
        # priority desc: Charlie 9, Alpha 5, Bravo 1
        assert names == ["Sort Src Charlie", "Sort Src Alpha", "Sort Src Bravo"]

    async def test_sort_by_name_asc_and_desc_are_reversed(self, client):
        fixture = await _seed_sort_fixture()

        asc = client.get(
            "/api/v1/sources?sort_by=name&sort_order=asc&page_size=100",
            headers=_headers(fixture["tenant_id"]),
        )
        desc = client.get(
            "/api/v1/sources?sort_by=name&sort_order=desc&page_size=100",
            headers=_headers(fixture["tenant_id"]),
        )
        assert asc.status_code == 200
        assert desc.status_code == 200

        asc_names = _names_for(asc.json()["data"], set(fixture["source_ids"].values()))
        desc_names = _names_for(desc.json()["data"], set(fixture["source_ids"].values()))
        assert asc_names == ["Sort Src Alpha", "Sort Src Bravo", "Sort Src Charlie"]
        assert desc_names == list(reversed(asc_names))

    async def test_invalid_sort_by_returns_400(self, client):
        fixture = await _seed_sort_fixture()
        resp = client.get("/api/v1/sources?sort_by=url", headers=_headers(fixture["tenant_id"]))
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"


class TestTenantSorting:
    def _create_tenants(self, client) -> dict[str, dict]:
        """Create three tenants sequentially; created_at increases monotonically."""
        created = {}
        suffix = uuid.uuid4().hex[:8]
        for name in (f"AAA Sort {suffix}", f"MMM Sort {suffix}", f"ZZZ Sort {suffix}"):
            resp = client.post(
                "/api/v1/admin/tenants",
                headers=_admin_headers(),
                json={"name": name, "slug": f"sort-{name.split()[0].lower()}-{uuid.uuid4().hex[:8]}"},
            )
            assert resp.status_code == 201
            created[name] = resp.json()["data"]
        return created

    def _list_tenant_names(self, client, wanted_ids: set[str], query: str = "") -> list[str]:
        resp = client.get(f"/api/v1/admin/tenants?page_size=100{query}", headers=_admin_headers())
        assert resp.status_code == 200
        return [row["name"] for row in resp.json()["data"] if row["id"] in wanted_ids]

    def test_default_order_is_created_at_desc(self, client):
        created = self._create_tenants(client)
        names = self._list_tenant_names(client, {t["id"] for t in created.values()})
        # newest first -> reverse creation order
        assert names == list(reversed(list(created.keys())))

    def test_sort_by_created_at_asc(self, client):
        created = self._create_tenants(client)
        names = self._list_tenant_names(
            client, {t["id"] for t in created.values()}, "&sort_by=created_at&sort_order=asc"
        )
        assert names == list(created.keys())

    def test_sort_by_name_asc_and_desc_are_reversed(self, client):
        created = self._create_tenants(client)
        wanted = {t["id"] for t in created.values()}

        asc_names = self._list_tenant_names(client, wanted, "&sort_by=name&sort_order=asc")
        desc_names = self._list_tenant_names(client, wanted, "&sort_by=name&sort_order=desc")
        assert asc_names == sorted(created.keys())
        assert desc_names == list(reversed(asc_names))

    def test_invalid_sort_by_returns_400(self, client):
        resp = client.get("/api/v1/admin/tenants?sort_by=slug", headers=_admin_headers())
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    def test_non_admin_cannot_sort_tenants(self, client):
        token = create_access_token(
            {
                "sub": str(uuid.uuid4()),
                "tenant_id": str(uuid.uuid4()),
                "role": "user",
                "provider": "github",
                "type": "access",
            }
        )
        resp = client.get(
            "/api/v1/admin/tenants?sort_by=name",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 403
