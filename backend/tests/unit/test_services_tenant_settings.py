"""Unit tests for tenant-level settings overrides (P2-16).

Covers extract_overrides tolerant parsing, the settings loaders' degradation
guarantee, TenantSettingsService validation/write semantics, and the API
layer wiring (GET read-back, non-admin PUT → 403) with dependencies stubbed.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.tenant import router as tenant_router
from app.core.exceptions import ValidationError
from app.schemas.tenant import TenantSettingsUpdate
from app.services.tenant import (
    TenantSettingsService,
    extract_overrides,
    load_all_tenant_settings,
    load_tenant_settings,
)


class TestExtractOverrides:
    def test_extracts_both_maps(self):
        settings = {
            "refresh_overrides": {"finance": 60, "tech": 300},
            "color_overrides": {"finance": "#FF0000"},
            "color_scheme": "international",
        }
        refresh, color = extract_overrides(settings)
        assert refresh == {"finance": 60, "tech": 300}
        assert color == {"finance": "#FF0000"}

    def test_missing_or_none_settings_yield_empty_maps(self):
        assert extract_overrides(None) == ({}, {})
        assert extract_overrides({}) == ({}, {})
        assert extract_overrides({"color_scheme": "chinese"}) == ({}, {})

    def test_non_dict_override_values_are_dropped(self):
        settings = {"refresh_overrides": [60], "color_overrides": "#FF0000"}
        assert extract_overrides(settings) == ({}, {})

    def test_malformed_entries_are_dropped_not_raised(self):
        settings = {
            "refresh_overrides": {"finance": 60, "tech": "300", "news": True},
            "color_overrides": {"finance": "#FF0000", "tech": 123},
        }
        refresh, color = extract_overrides(settings)
        assert refresh == {"finance": 60}
        assert color == {"finance": "#FF0000"}


class TestLoadTenantSettingsDegradation:
    async def test_returns_settings(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = {"refresh_overrides": {"finance": 60}}
        session.execute = AsyncMock(return_value=result)

        settings = await load_tenant_settings(session, str(uuid.uuid4()))
        assert settings == {"refresh_overrides": {"finance": 60}}

    async def test_none_settings_become_empty_dict(self):
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session.execute = AsyncMock(return_value=result)

        assert await load_tenant_settings(session, str(uuid.uuid4())) == {}

    async def test_db_failure_degrades_to_empty_dict(self):
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("boom"))
        assert await load_tenant_settings(session, str(uuid.uuid4())) == {}

    async def test_load_all_maps_every_tenant(self):
        session = AsyncMock()
        t1, t2 = uuid.uuid4(), uuid.uuid4()
        result = MagicMock()
        result.all.return_value = [(t1, {"a": 1}), (t2, None)]
        session.execute = AsyncMock(return_value=result)

        loaded = await load_all_tenant_settings(session)
        assert loaded == {str(t1): {"a": 1}, str(t2): {}}

    async def test_load_all_db_failure_degrades_to_empty_dict(self):
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=RuntimeError("boom"))
        assert await load_all_tenant_settings(session) == {}


def _make_tenant(settings=None):
    tenant = MagicMock()
    tenant.id = uuid.uuid4()
    tenant.settings = settings if settings is not None else {}
    return tenant


def _service_with_db(tenant, valid_slugs):
    """Scripted db: execute #1 → tenant row, execute #2 → category slugs."""
    db = AsyncMock()
    tenant_result = MagicMock()
    tenant_result.scalar_one_or_none.return_value = tenant
    slugs_result = MagicMock()
    slugs_result.scalars.return_value.all.return_value = list(valid_slugs)
    db.execute = AsyncMock(side_effect=[tenant_result, slugs_result])
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    return db


class TestUpdateOverridesValidation:
    async def test_invalid_refresh_range_rejected(self):
        tenant = _make_tenant()
        db = _service_with_db(tenant, ["finance"])
        service = TenantSettingsService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.update_overrides(str(tenant.id), TenantSettingsUpdate(refresh_overrides={"finance": 5}))
        details = exc_info.value.error_details
        assert details[0]["field"] == "refresh_overrides.finance"
        assert "between 10 and 86400" in details[0]["message"]
        db.flush.assert_not_awaited()

    async def test_invalid_color_rejected(self):
        tenant = _make_tenant()
        db = _service_with_db(tenant, ["finance"])
        service = TenantSettingsService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.update_overrides(str(tenant.id), TenantSettingsUpdate(color_overrides={"finance": "red"}))
        details = exc_info.value.error_details
        assert details[0]["field"] == "color_overrides.finance"
        assert "#RRGGBB" in details[0]["message"]

    async def test_unknown_slug_rejected(self):
        tenant = _make_tenant()
        db = _service_with_db(tenant, ["finance"])
        service = TenantSettingsService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.update_overrides(str(tenant.id), TenantSettingsUpdate(refresh_overrides={"ghost": 60}))
        details = exc_info.value.error_details
        assert details[0]["field"] == "refresh_overrides.ghost"
        assert "Unknown category slug" in details[0]["message"]

    async def test_multiple_errors_reported_together(self):
        tenant = _make_tenant()
        db = _service_with_db(tenant, ["finance"])
        service = TenantSettingsService(db)

        with pytest.raises(ValidationError) as exc_info:
            await service.update_overrides(
                str(tenant.id),
                TenantSettingsUpdate(
                    refresh_overrides={"ghost": 60, "finance": 5},
                    color_overrides={"tech": "nope"},
                ),
            )
        fields = {d["field"] for d in exc_info.value.error_details}
        assert fields == {
            "refresh_overrides.ghost",
            "refresh_overrides.finance",
            "color_overrides.tech",
        }

    async def test_tenant_not_found_rejected(self):
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result)
        service = TenantSettingsService(db)

        with pytest.raises(ValidationError, match="Tenant not found"):
            await service.update_overrides(str(uuid.uuid4()), TenantSettingsUpdate())

    async def test_valid_write_replaces_only_override_keys(self):
        tenant = _make_tenant(settings={"color_scheme": "international", "legacy_flag": True})
        db = _service_with_db(tenant, ["finance", "tech"])
        service = TenantSettingsService(db)

        result = await service.update_overrides(
            str(tenant.id),
            TenantSettingsUpdate(
                refresh_overrides={"finance": 60},
                color_overrides={"finance": "#FF0000"},
            ),
        )

        db.flush.assert_awaited_once()
        assert result.data.refresh_overrides == {"finance": 60}
        assert result.data.color_overrides == {"finance": "#FF0000"}
        # Other settings keys are preserved by the whole-replace.
        assert tenant.settings["color_scheme"] == "international"
        assert tenant.settings["legacy_flag"] is True

    async def test_omitted_keys_reset_to_empty(self):
        """Whole-replace semantics: sending only one override key resets the
        other one to {} (the request body is the new complete state)."""
        tenant = _make_tenant(
            settings={"refresh_overrides": {"finance": 60}, "color_overrides": {"finance": "#FF0000"}}
        )
        db = _service_with_db(tenant, ["finance"])
        service = TenantSettingsService(db)

        result = await service.update_overrides(
            str(tenant.id), TenantSettingsUpdate(color_overrides={"finance": "#00FF00"})
        )

        assert result.data.refresh_overrides == {}
        assert result.data.color_overrides == {"finance": "#00FF00"}
        assert tenant.settings["refresh_overrides"] == {}


class TestGetOverrides:
    async def test_reads_stored_overrides(self):
        tenant = _make_tenant(settings={"refresh_overrides": {"finance": 60}, "color_overrides": {"tech": "#00FF00"}})
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = tenant
        db.execute = AsyncMock(return_value=result)
        service = TenantSettingsService(db)

        response = await service.get_overrides(str(tenant.id))
        assert response.data.refresh_overrides == {"finance": 60}
        assert response.data.color_overrides == {"tech": "#00FF00"}

    async def test_empty_settings_yield_empty_maps(self):
        tenant = _make_tenant(settings={})
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = tenant
        db.execute = AsyncMock(return_value=result)
        service = TenantSettingsService(db)

        response = await service.get_overrides(str(tenant.id))
        assert response.data.refresh_overrides == {}
        assert response.data.color_overrides == {}


def _make_app(db, role="admin"):
    """Test app with auth dependencies stubbed (role controls the 403 path)."""
    app = FastAPI()
    app.include_router(tenant_router, prefix="/api/v1/tenant")

    async def override_get_db():
        yield db

    async def override_get_current_user():
        return {"user_id": "user-1", "tenant_id": "tenant-1", "role": role, "provider": "github"}

    from app.dependencies import get_current_user, get_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    return app


class TestTenantSettingsApi:
    def test_get_returns_stored_overrides(self):
        tenant = _make_tenant(
            settings={"refresh_overrides": {"finance": 60}, "color_overrides": {"finance": "#FF0000"}}
        )
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = tenant
        db.execute = AsyncMock(return_value=result)

        client = TestClient(_make_app(db, role="member"))
        resp = client.get("/api/v1/tenant/settings")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"]["refresh_overrides"] == {"finance": 60}
        assert body["data"]["color_overrides"] == {"finance": "#FF0000"}

    def test_get_readable_by_plain_member(self):
        tenant = _make_tenant()
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = tenant
        db.execute = AsyncMock(return_value=result)

        client = TestClient(_make_app(db, role="viewer"))
        resp = client.get("/api/v1/tenant/settings")
        assert resp.status_code == 200

    def test_put_as_member_forbidden(self):
        db = AsyncMock()
        client = TestClient(_make_app(db, role="member"))
        resp = client.put("/api/v1/tenant/settings", json={"refresh_overrides": {"finance": 60}})

        assert resp.status_code == 403
        error = resp.json()["detail"]["error"]
        assert error["code"] == "FORBIDDEN"

    def test_put_as_admin_writes_and_echoes(self):
        tenant = _make_tenant(settings={"color_scheme": "international"})
        db = _service_with_db(tenant, ["finance"])

        client = TestClient(_make_app(db, role="admin"))
        resp = client.put(
            "/api/v1/tenant/settings",
            json={"refresh_overrides": {"finance": 60}, "color_overrides": {"finance": "#FF0000"}},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["data"]["refresh_overrides"] == {"finance": 60}
        assert body["data"]["color_overrides"] == {"finance": "#FF0000"}
        db.flush.assert_awaited_once()

    def test_put_invalid_override_returns_400_with_details(self):
        tenant = _make_tenant()
        db = _service_with_db(tenant, ["finance"])

        client = TestClient(_make_app(db, role="admin"), raise_server_exceptions=False)
        resp = client.put("/api/v1/tenant/settings", json={"color_overrides": {"finance": "red"}})

        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == "color_overrides.finance"
