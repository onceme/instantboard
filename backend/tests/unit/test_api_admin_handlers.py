"""Unit tests for admin.py handler bodies — directly calling handler functions."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.admin import (
    _tenant_to_response,
    create_tenant,
    delete_tenant,
    get_tenant,
    get_tenant_stats,
    list_tenants,
    require_admin,
    update_tenant,
)
from app.core.exceptions import Forbidden, ValidationError
from app.schemas.admin import TenantCreate, TenantUpdate
from app.schemas.base import PaginationParams


def _make_tenant(**overrides):
    t = MagicMock()
    t.id = overrides.get("id", uuid.uuid4())
    t.name = overrides.get("name", "TestCorp")
    t.slug = overrides.get("slug", "testcorp")
    t.plan = overrides.get("plan", "free")
    t.settings = overrides.get("settings", {})
    t.max_users = overrides.get("max_users", 5)
    t.max_categories = overrides.get("max_categories", 10)
    t.max_sources = overrides.get("max_sources", 50)
    t.is_active = overrides.get("is_active", True)
    t.created_at = overrides.get("created_at", datetime.now(UTC))
    t.updated_at = overrides.get("updated_at", datetime.now(UTC))
    return t


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    return db, mock_result


class TestRequireAdmin:
    def test_admin_passes(self):
        require_admin({"role": "admin"})  # no exception

    def test_non_admin_raises(self):
        with pytest.raises(Forbidden, match="Admin access required"):
            require_admin({"role": "user"})

    def test_no_role_raises(self):
        with pytest.raises(Forbidden):
            require_admin({})


class TestTenantToResponse:
    def test_converts_tenant(self):
        t = _make_tenant()
        resp = _tenant_to_response(t)
        assert resp.name == "TestCorp"
        assert resp.slug == "testcorp"

    def test_none_settings_becomes_empty_dict(self):
        t = _make_tenant(settings=None)
        resp = _tenant_to_response(t)
        assert resp.settings == {}


class TestCreateTenant:
    async def test_create_success(self):
        db, mock_result = _mock_db()
        mock_result.scalar_one_or_none.return_value = None  # no duplicate

        # Simulate database behavior: db.refresh populates server-side defaults
        def refresh_side_effect(tenant):
            if tenant.is_active is None:
                tenant.is_active = True
            if tenant.created_at is None:
                tenant.created_at = datetime.now(UTC)
            if tenant.updated_at is None:
                tenant.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=refresh_side_effect)

        req = TenantCreate(name="New Corp", slug="new-corp", plan="free")
        resp = await create_tenant(req, db=db, user={"role": "admin"})
        assert resp.success is True
        assert resp.data.name == "New Corp"

    async def test_create_duplicate_slug(self):
        db, mock_result = _mock_db()
        existing = _make_tenant(slug="dup-slug")
        mock_result.scalar_one_or_none.return_value = existing

        req = TenantCreate(name="Dup", slug="dup-slug")
        with pytest.raises(ValidationError, match="already exists"):
            await create_tenant(req, db=db, user={"role": "admin"})

    async def test_create_non_admin_forbidden(self):
        db, _ = _mock_db()
        req = TenantCreate(name="X", slug="x")
        with pytest.raises(Forbidden):
            await create_tenant(req, db=db, user={"role": "user"})

    async def test_create_with_settings(self):
        db, mock_result = _mock_db()
        mock_result.scalar_one_or_none.return_value = None

        # Simulate database behavior: db.refresh populates server-side defaults
        def refresh_side_effect(tenant):
            if tenant.is_active is None:
                tenant.is_active = True
            if tenant.created_at is None:
                tenant.created_at = datetime.now(UTC)
            if tenant.updated_at is None:
                tenant.updated_at = datetime.now(UTC)

        db.refresh = AsyncMock(side_effect=refresh_side_effect)

        req = TenantCreate(name="X", slug="x", settings={"theme": "dark"})
        resp = await create_tenant(req, db=db, user={"role": "admin"})
        assert resp.data.settings == {"theme": "dark"}


class TestListTenants:
    async def test_list_success(self):
        db, mock_result = _mock_db()

        # First call: count query
        count_result = MagicMock()
        count_result.scalar.return_value = 2

        # Second call: select query
        tenants = [_make_tenant(name="A"), _make_tenant(name="B")]
        select_result = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = tenants
        select_result.scalars.return_value = scalars

        call_count = 0

        async def execute_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            return count_result if call_count == 1 else select_result

        db.execute = execute_side_effect

        resp = await list_tenants(pagination=PaginationParams(page=1, page_size=20), db=db, user={"role": "admin"})
        assert resp.success is True
        assert len(resp.data) == 2
        assert resp.meta.total == 2

    async def test_list_empty(self):
        db, mock_result = _mock_db()

        count_result = MagicMock()
        count_result.scalar.return_value = 0

        select_result = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = []
        select_result.scalars.return_value = scalars

        call_count = 0

        async def execute_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            return count_result if call_count == 1 else select_result

        db.execute = execute_side_effect

        resp = await list_tenants(pagination=PaginationParams(page=1, page_size=20), db=db, user={"role": "admin"})
        assert resp.meta.total == 0
        assert len(resp.data) == 0


class TestGetTenant:
    async def test_get_found(self):
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid)
        mock_result.scalar_one_or_none.return_value = tenant

        resp = await get_tenant(str(tid), db=db, user={"role": "admin"})
        assert resp.data.id == str(tid)

    async def test_get_not_found(self):
        from app.core.exceptions import AppException

        db, mock_result = _mock_db()
        mock_result.scalar_one_or_none.return_value = None

        tid = str(uuid.uuid4())
        with pytest.raises(AppException) as exc_info:
            await get_tenant(tid, db=db, user={"role": "admin"})
        assert exc_info.value.status_code == 404

    async def test_get_invalid_uuid(self):
        db, _ = _mock_db()
        with pytest.raises(ValidationError, match="Invalid tenant ID"):
            await get_tenant("not-a-uuid", db=db, user={"role": "admin"})


class TestUpdateTenant:
    async def test_update_success(self):
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid, name="Old")
        mock_result.scalar_one_or_none.return_value = tenant

        req = TenantUpdate(name="New Name")
        resp = await update_tenant(str(tid), req, db=db, user={"role": "admin"})
        assert resp.data.name == "New Name"

    async def test_update_not_found(self):
        from app.core.exceptions import AppException

        db, mock_result = _mock_db()
        mock_result.scalar_one_or_none.return_value = None

        with pytest.raises(AppException) as exc_info:
            await update_tenant(str(uuid.uuid4()), TenantUpdate(name="X"), db=db, user={"role": "admin"})
        assert exc_info.value.status_code == 404

    async def test_update_invalid_uuid(self):
        db, _ = _mock_db()
        with pytest.raises(ValidationError, match="Invalid tenant ID"):
            await update_tenant("bad", TenantUpdate(), db=db, user={"role": "admin"})

    async def test_update_duplicate_slug(self):
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid, slug="old-slug")
        existing = _make_tenant(slug="taken-slug")

        call_count = 0

        async def execute_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = tenant
            elif call_count == 2:
                r.scalar_one_or_none.return_value = existing
            return r

        db.execute = execute_side_effect

        req = TenantUpdate(slug="taken-slug")
        with pytest.raises(ValidationError, match="already exists"):
            await update_tenant(str(tid), req, db=db, user={"role": "admin"})

    async def test_update_none_slug_skipped(self):
        """When slug is in update_data but is None/empty, skip duplicate check."""
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid)

        call_count = 0

        async def execute_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            r.scalar_one_or_none.return_value = tenant
            return r

        db.execute = execute_side_effect

        req = TenantUpdate(name="Updated")
        resp = await update_tenant(str(tid), req, db=db, user={"role": "admin"})
        assert resp.data.name == "Updated"


class TestDeleteTenant:
    async def test_delete_success(self):
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid, slug="delete-me")
        mock_result.scalar_one_or_none.return_value = tenant

        resp = await delete_tenant(str(tid), db=db, user={"role": "admin"})
        assert resp.status_code == 204
        db.delete.assert_called_once_with(tenant)

    async def test_delete_not_found(self):
        from app.core.exceptions import AppException

        db, mock_result = _mock_db()
        mock_result.scalar_one_or_none.return_value = None

        with pytest.raises(AppException) as exc_info:
            await delete_tenant(str(uuid.uuid4()), db=db, user={"role": "admin"})
        assert exc_info.value.status_code == 404

    async def test_delete_invalid_uuid(self):
        db, _ = _mock_db()
        with pytest.raises(ValidationError, match="Invalid tenant ID"):
            await delete_tenant("bad-id", db=db, user={"role": "admin"})

    async def test_delete_system_tenant_forbidden(self):
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid, slug="system")
        mock_result.scalar_one_or_none.return_value = tenant

        with pytest.raises(Forbidden, match="Cannot delete the system tenant"):
            await delete_tenant(str(tid), db=db, user={"role": "admin"})


class TestGetTenantStats:
    async def test_stats_success(self):
        db, mock_result = _mock_db()
        tid = uuid.uuid4()
        tenant = _make_tenant(id=tid)

        call_count = 0

        async def execute_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = tenant
            elif call_count in (2, 3, 4, 5):
                r.scalar.return_value = call_count - 1  # 1,2,3,4
            return r

        db.execute = execute_side_effect

        with patch("app.core.sse_router.event_router") as mock_router:
            mock_router.get_stats.return_value = {"total_connections": 7}
            resp = await get_tenant_stats(str(tid), db=db, user={"role": "admin"})

            assert resp.data.tenant_id == str(tid)
            assert resp.data.user_count == 1
            assert resp.data.category_count == 2
            assert resp.data.source_count == 3
            assert resp.data.item_count == 4
            assert resp.data.active_sse_connections == 7

    async def test_stats_not_found(self):
        from app.core.exceptions import AppException

        db, mock_result = _mock_db()

        call_count = 0

        async def execute_side_effect(*a, **kw):
            nonlocal call_count
            call_count += 1
            r = MagicMock()
            if call_count == 1:
                r.scalar_one_or_none.return_value = None
            return r

        db.execute = execute_side_effect

        with pytest.raises(AppException) as exc_info:
            await get_tenant_stats(str(uuid.uuid4()), db=db, user={"role": "admin"})
        assert exc_info.value.status_code == 404

    async def test_stats_invalid_uuid(self):
        db, _ = _mock_db()
        with pytest.raises(ValidationError, match="Invalid tenant ID"):
            await get_tenant_stats("bad", db=db, user={"role": "admin"})

    async def test_stats_non_admin(self):
        db, _ = _mock_db()
        with pytest.raises(Forbidden):
            await get_tenant_stats(str(uuid.uuid4()), db=db, user={"role": "user"})
