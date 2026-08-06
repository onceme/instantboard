import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import CategoryNotFound, Forbidden, SourceNotFound, ValidationError
from app.services.source import SYSTEM_TENANT_ID, SourceService, _health_to_response, _source_to_response


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    return db, mock_result


def _mock_redis():
    return AsyncMock()


def _make_source(
    source_id=None, tenant_id="tenant-1", category_id=None,
    name="Test Source", source_type="rss", url="https://example.com/rss",
    config=None, refresh_interval_seconds=300, is_active=True, priority=5,
):
    src = MagicMock()
    src.id = source_id or uuid.uuid4()
    src.tenant_id = tenant_id
    src.category_id = category_id or uuid.uuid4()
    src.name = name
    src.source_type = source_type
    src.url = url
    src.config = config or {}
    src.refresh_interval_seconds = refresh_interval_seconds
    src.is_active = is_active
    src.priority = priority
    src.created_at = datetime.now(UTC)
    src.updated_at = datetime.now(UTC)
    src.health = None
    src.category = MagicMock()
    src.category.refresh_interval_seconds = 300
    src.category.slug = "test"
    return src


def _make_health(
    source_id=None, status="healthy", consecutive_failures=0,
    total_fetches_24h=10, success_count_24h=10, avg_response_time_ms=50,
    last_success_at=None, last_failure_at=None, last_error_message=None,
):
    h = MagicMock()
    h.source_id = source_id or uuid.uuid4()
    h.status = status
    h.consecutive_failures = consecutive_failures
    h.total_fetches_24h = total_fetches_24h
    h.success_count_24h = success_count_24h
    h.avg_response_time_ms = avg_response_time_ms
    h.last_success_at = last_success_at or datetime.now(UTC)
    h.last_failure_at = last_failure_at
    h.last_error_message = last_error_message
    h.updated_at = datetime.now(UTC)
    return h


class TestSourceToResponse:
    def test_basic(self):
        src = _make_source()
        resp = _source_to_response(src)
        assert resp.id == str(src.id)
        assert resp.health_status is None

    def test_with_health(self):
        src = _make_source()
        health = _make_health(source_id=src.id, status="degraded")
        src.health = health
        resp = _source_to_response(src)
        assert resp.health_status == "degraded"

    def test_null_refresh_interval(self):
        src = _make_source()
        src.refresh_interval_seconds = None
        resp = _source_to_response(src)
        assert resp.refresh_interval_seconds == 300

    def test_null_refresh_interval_no_category(self):
        src = _make_source()
        src.refresh_interval_seconds = None
        src.category = None
        resp = _source_to_response(src)
        assert resp.refresh_interval_seconds == 300


class TestHealthToResponse:
    def test_with_success_rate(self):
        h = _make_health(total_fetches_24h=100, success_count_24h=90)
        resp = _health_to_response(h)
        assert resp.success_rate_24h == 0.9

    def test_no_fetches(self):
        h = _make_health(total_fetches_24h=0)
        resp = _health_to_response(h)
        assert resp.success_rate_24h is None


class TestValidateSourceConfig:
    def test_empty_config(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = SourceService(db, redis)
        assert service._validate_source_config("rss", {}) is True
        assert service._validate_source_config("rss", None) is True

    def test_rss_missing_url(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="url"):
            service._validate_source_config("rss", {"other": "field"})

    def test_api_missing_fields(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="url"):
            service._validate_source_config("api", {"other": "field"})

    def test_web_scrape_missing_fields(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="url"):
            service._validate_source_config("web_scrape", {"other": "x"})

    def test_social_missing_fields(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="platform"):
            service._validate_source_config("social", {"other": "x"})

    def test_unknown_type(self):
        db, _ = _mock_db()
        redis = _mock_redis()
        service = SourceService(db, redis)
        assert service._validate_source_config("unknown_type", {}) is True


class TestListSources:
    async def test_basic_list(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar.return_value = 1
            elif call_count == 2:
                scalars = MagicMock()
                scalars.all.return_value = [src]
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = SourceService(db, redis)
        result = await service.list_sources("tenant-1")
        assert result.success is True
        assert len(result.data) == 1

    async def test_list_with_filters(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_result.scalar.return_value = 0
        scalars = MagicMock()
        scalars.all.return_value = []
        mock_result.scalars.return_value = scalars

        service = SourceService(db, redis)
        result = await service.list_sources(
            "tenant-1",
            category_id=str(uuid.uuid4()),
            source_type="rss",
            is_active=True,
        )
        assert result.meta.total == 0

    async def test_list_with_status_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_result.scalar.return_value = 0
        scalars = MagicMock()
        scalars.all.return_value = []
        mock_result.scalars.return_value = scalars

        service = SourceService(db, redis)
        result = await service.list_sources("tenant-1", status_filter="healthy")
        assert result.meta.total == 0


class TestGetSource:
    async def test_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id="tenant-1")
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        result = await service.get_source(str(src.id), "tenant-1")
        assert result.success is True

    async def test_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = SourceService(db, redis)
        with pytest.raises(SourceNotFound):
            await service.get_source("bad-id", "tenant-1")

    async def test_wrong_tenant(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        with pytest.raises(SourceNotFound, match="not accessible"):
            await service.get_source(str(src.id), "tenant-1")

    async def test_system_source_accessible(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        result = await service.get_source(str(src.id), "tenant-1")
        assert result.success is True


class TestCreateSource:
    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_create_success(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.id = "tenant-1"
        tenant.max_sources = 50

        category = MagicMock()
        category.id = uuid.uuid4()
        category.tenant_id = "tenant-1"
        category.refresh_interval_seconds = 300

        created_source = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = tenant
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = category
            elif call_count == 4:
                mock_r.scalar_one.return_value = created_source
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceCreate

        data = SourceCreate(
            name="Test RSS",
            category_id=str(category.id),
            source_type="rss",
            url="https://example.com/rss",
        )

        service = SourceService(db, redis)
        result = await service.create_source(data, "tenant-1")
        assert result.success is True

    async def test_create_tenant_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        from app.schemas.source import SourceCreate

        data = SourceCreate(name="Test", category_id=str(uuid.uuid4()), source_type="rss", url="https://x.com/rss")

        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="Tenant not found"):
            await service.create_source(data, "bad-tenant")

    async def test_create_limit_reached(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.max_sources = 2

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = tenant
            elif call_count == 2:
                mock_r.scalar.return_value = 3
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceCreate

        data = SourceCreate(name="Test", category_id=str(uuid.uuid4()), source_type="rss", url="https://x.com/rss")

        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="Source limit reached"):
            await service.create_source(data, "tenant-1")

    async def test_create_category_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.max_sources = 50

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = tenant
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = None
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceCreate

        data = SourceCreate(name="Test", category_id=str(uuid.uuid4()), source_type="rss", url="https://x.com/rss")

        service = SourceService(db, redis)
        with pytest.raises(CategoryNotFound, match="category not found"):
            await service.create_source(data, "tenant-1")

    async def test_create_category_wrong_tenant(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.max_sources = 50

        category = MagicMock()
        category.tenant_id = "other-tenant"

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = tenant
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            elif call_count == 3:
                mock_r.scalar_one_or_none.return_value = category
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceCreate

        data = SourceCreate(name="Test", category_id=str(uuid.uuid4()), source_type="rss", url="https://x.com/rss")

        service = SourceService(db, redis)
        with pytest.raises(ValidationError, match="other tenants"):
            await service.create_source(data, "tenant-1")


class TestUpdateSource:
    async def test_update_success(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = src
            elif call_count == 2:
                mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(name="Updated Source")

        service = SourceService(db, redis)
        result = await service.update_source(str(src.id), data, "tenant-1")
        assert result.success is True

    async def test_update_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(name="Updated")

        service = SourceService(db, redis)
        with pytest.raises(SourceNotFound):
            await service.update_source("bad-id", data, "tenant-1")

    async def test_update_other_tenant_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = src

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(name="Updated")

        service = SourceService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.update_source(str(src.id), data, "tenant-1")

    async def test_update_system_source_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = src

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(name="Updated")

        service = SourceService(db, redis)
        with pytest.raises(Forbidden, match="system-level"):
            await service.update_source(str(src.id), data, SYSTEM_TENANT_ID)

    async def test_update_with_source_type_validation(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = src
            elif call_count == 2:
                mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(source_type="api", config={"url": "https://api.com", "method": "GET"})

        service = SourceService(db, redis)
        result = await service.update_source(str(src.id), data, "tenant-1")
        assert result.success is True


class TestDeleteSource:
    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_delete", new_callable=AsyncMock)
    async def test_delete_success(self, mock_del, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        await service.delete_source(str(src.id), "tenant-1")
        db.delete.assert_called_once()

    async def test_delete_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = SourceService(db, redis)
        with pytest.raises(SourceNotFound):
            await service.delete_source("bad-id", "tenant-1")

    async def test_delete_other_tenant_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.delete_source(str(src.id), "tenant-1")

    async def test_delete_system_source_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        with pytest.raises(Forbidden, match="system-level"):
            await service.delete_source(str(src.id), SYSTEM_TENANT_ID)

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_delete", new_callable=AsyncMock)
    async def test_delete_no_category(self, mock_del, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")
        src.category = None
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        await service.delete_source(str(src.id), "tenant-1")
        mock_publish.assert_called()


class TestGetSourceHealth:
    async def test_health_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health()
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        service = SourceService(db, redis)
        result = await service.get_source_health(str(src.id), "tenant-1")
        assert result.success is True

    async def test_health_not_found_source_exists(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = None
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        service = SourceService(db, redis)
        result = await service.get_source_health(str(src.id), "tenant-1")
        assert result.data.status == "healthy"

    async def test_health_not_found_source_not_exists(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = SourceService(db, redis)
        with pytest.raises(SourceNotFound):
            await service.get_source_health("bad-id", "tenant-1")

    async def test_health_wrong_tenant(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health()
        src = _make_source(tenant_id="other-tenant")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        service = SourceService(db, redis)
        with pytest.raises(SourceNotFound, match="not accessible"):
            await service.get_source_health(str(src.id), "tenant-1")


class TestUpdateSourceHealth:
    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_success_healthy(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health(status="healthy", consecutive_failures=0, total_fetches_24h=5)
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=True, response_time_ms=100)

        service = SourceService(db, redis)
        result = await service.update_source_health(str(src.id), result_obj)
        assert result.success is True

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_failure_degraded(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health(status="healthy", consecutive_failures=0, total_fetches_24h=5)
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=False, response_time_ms=0, error_message="timeout")

        service = SourceService(db, redis)
        result = await service.update_source_health(str(src.id), result_obj)
        assert result.success is True

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_failure_down(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health(status="degraded", consecutive_failures=9, total_fetches_24h=15)
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=False, error_message="connection refused")

        service = SourceService(db, redis)
        result = await service.update_source_health(str(src.id), result_obj)
        assert result.data.status == "down"

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_creates_health_if_none(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = None
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=True, response_time_ms=50)

        service = SourceService(db, redis)
        result = await service.update_source_health(str(src.id), result_obj)
        assert result.success is True
        db.add.assert_called()

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_status_change_publishes(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health(status="down", consecutive_failures=5, total_fetches_24h=10)
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=True, response_time_ms=50)

        service = SourceService(db, redis)
        await service.update_source_health(str(src.id), result_obj)
        assert mock_publish.call_count == 2

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_degraded_after_down(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health(status="down", consecutive_failures=10, total_fetches_24h=20)
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=True, response_time_ms=50)

        service = SourceService(db, redis)
        result = await service.update_source_health(str(src.id), result_obj)
        assert result.data.status == "degraded"

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_hset", new_callable=AsyncMock)
    async def test_update_no_status_change_no_publish(self, mock_hset, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        health = _make_health(status="healthy", consecutive_failures=0, total_fetches_24h=5)
        src = _make_source(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = health
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import HealthCheckResult

        result_obj = HealthCheckResult(success=True, response_time_ms=50)

        service = SourceService(db, redis)
        await service.update_source_health(str(src.id), result_obj)
        mock_publish.assert_not_called()


class TestGetAllSourcesHealthSummary:
    async def test_summary(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.all.return_value = [("healthy", 5), ("degraded", 2)]
            elif call_count == 2:
                mock_r.scalar.return_value = 8
            return mock_r

        db.execute = execute_side_effect

        service = SourceService(db, redis)
        result = await service.get_all_sources_health_summary("tenant-1")
        assert result["total_sources"] == 8
        assert result["healthy"] == 5
        assert result["degraded"] == 2
