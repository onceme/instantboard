import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import (
    CategoryNotFound,
    Forbidden,
    NoCollectorAvailable,
    SourceNotFound,
    ValidationError,
)
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
    source_id=None,
    tenant_id="tenant-1",
    category_id=None,
    name="Test Source",
    source_type="rss",
    url="https://example.com/rss",
    config=None,
    refresh_interval_seconds=300,
    is_active=True,
    priority=5,
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
    source_id=None,
    status="healthy",
    consecutive_failures=0,
    total_fetches_24h=10,
    success_count_24h=10,
    avg_response_time_ms=50,
    last_success_at=None,
    last_failure_at=None,
    last_error_message=None,
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
    @patch("app.services.source.redis_set", new_callable=AsyncMock)
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

    # ── collector pre-flight on create ───────────────────────────
    async def test_create_active_without_collector_rejected(self):
        """web_scrape sources have no collector of their own and no config.library
        fallback here → creating them active must fail loudly instead of
        silently scheduling a job that can never collect."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.max_sources = 50
        category = MagicMock()
        category.tenant_id = "tenant-1"
        category.refresh_interval_seconds = 300

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

        data = SourceCreate(
            name="Bare scraper",
            category_id=str(uuid.uuid4()),
            source_type="web_scrape",
            url="https://example.com",
            config={"url": "https://example.com", "selector": ".item"},
            is_active=True,
        )

        service = SourceService(db, redis)
        with pytest.raises(NoCollectorAvailable, match="web_scrape"):
            await service.create_source(data, "tenant-1")
        db.add.assert_not_called()

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_set", new_callable=AsyncMock)
    async def test_create_active_with_library_collector_ok(self, mock_hset, mock_publish):
        """Same web_scrape source but with a config.library fallback → resolves to a
        real collector and may be created active."""
        db, _ = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.max_sources = 50
        category = MagicMock()
        category.id = uuid.uuid4()
        category.tenant_id = "tenant-1"
        category.refresh_interval_seconds = 300
        created_source = _make_source(tenant_id="tenant-1", source_type="web_scrape")

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
            name="Scraper with library",
            category_id=str(category.id if hasattr(category, "id") else uuid.uuid4()),
            source_type="web_scrape",
            url="https://example.com",
            config={"url": "https://example.com", "selector": ".item", "library": "eastmoney"},
            is_active=True,
        )

        service = SourceService(db, redis)
        result = await service.create_source(data, "tenant-1")
        assert result.success is True

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_set", new_callable=AsyncMock)
    async def test_create_inactive_without_collector_allowed(self, mock_hset, mock_publish):
        """Inactive sources may be created without a collector (draft state)."""
        db, _ = _mock_db()
        redis = _mock_redis()

        tenant = MagicMock()
        tenant.max_sources = 50
        category = MagicMock()
        category.tenant_id = "tenant-1"
        category.refresh_interval_seconds = 300
        created_source = _make_source(tenant_id="tenant-1", source_type="web_scrape", is_active=False)

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
            name="Draft scraper",
            category_id=str(uuid.uuid4()),
            source_type="web_scrape",
            url="https://example.com",
            config={"url": "https://example.com", "selector": ".item"},
            is_active=False,
        )

        service = SourceService(db, redis)
        result = await service.create_source(data, "tenant-1")
        assert result.success is True


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

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_update_system_source_by_owning_admin_tenant_allowed(self, mock_publish):
        """System (seed) sources are editable by the tenant that owns them — the admin
        session lives in the system tenant itself and must be able to enable seeded
        sources (e.g. 东方财富/yfinance) via the sources API."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id=SYSTEM_TENANT_ID, is_active=False)

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

        data = SourceUpdate(is_active=True)

        service = SourceService(db, redis)
        # The API passes the JWT tenant id as a str — must match the UUID ORM attribute
        result = await service.update_source(str(src.id), data, str(SYSTEM_TENANT_ID))
        assert result.success is True
        assert src.is_active is True

    async def test_update_system_source_other_tenant_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        src = _make_source(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = src

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(name="Updated")

        service = SourceService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.update_source(str(src.id), data, "tenant-1")

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

    # ── collector pre-flight on enable ───────────────────────────
    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_enable_without_collector_rejected(self, mock_publish):
        """Flipping is_active to True on a source no collector can run must return
        the NO_COLLECTOR_AVAILABLE error instead of silently succeeding."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(
            tenant_id="tenant-1",
            source_type="web_scrape",
            config={"url": "https://example.com", "selector": ".item"},
            is_active=False,
        )
        mock_result.scalar_one_or_none.return_value = src

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(is_active=True)

        service = SourceService(db, redis)
        with pytest.raises(NoCollectorAvailable, match="web_scrape"):
            await service.update_source(str(src.id), data, "tenant-1")
        mock_publish.assert_not_called()

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_enable_checks_effective_type_after_same_request_change(self, mock_publish):
        """is_active=True combined with source_type=web_scrape in the same request must
        be validated against the effective (new) type, not the current one."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1", source_type="rss", is_active=False)
        mock_result.scalar_one_or_none.return_value = src

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(
            is_active=True,
            source_type="web_scrape",
            config={"url": "https://example.com", "selector": ".item"},
        )

        service = SourceService(db, redis)
        with pytest.raises(NoCollectorAvailable, match="web_scrape"):
            await service.update_source(str(src.id), data, "tenant-1")
        mock_publish.assert_not_called()

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_enable_with_library_fallback_ok(self, mock_publish):
        """web_scrape + config.library=eastmoney resolves to a real collector → the
        enable is allowed and publishes source_enabled for the worker."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(
            tenant_id="tenant-1",
            source_type="web_scrape",
            config={"url": "https://example.com", "selector": ".item", "library": "eastmoney"},
            is_active=False,
        )

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar_one_or_none.return_value = src
            mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(is_active=True)

        service = SourceService(db, redis)
        result = await service.update_source(str(src.id), data, "tenant-1")
        assert result.success is True
        assert src.is_active is True
        mock_publish.assert_awaited_once()
        channel, message = mock_publish.call_args.args
        assert channel == "channel:dashboard"
        assert message["event"] == "source_enabled"

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_disable_with_type_change_to_uncollectable_not_blocked(self, mock_publish):
        """Disabling must never be blocked by the collector pre-flight, even when the
        same request switches source_type to one no collector can run. Guards against
        the pre-flight gate regressing from `is True` to `is not None` (which would
        reject every plain disable of an uncollectable source)."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1", source_type="rss", is_active=True)

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar_one_or_none.return_value = src
            mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        data = SourceUpdate(
            is_active=False,
            source_type="web_scrape",
            config={"url": "https://example.com", "selector": ".item"},
        )

        service = SourceService(db, redis)
        result = await service.update_source(str(src.id), data, "tenant-1")
        assert result.success is True
        assert src.is_active is False
        mock_publish.assert_awaited_once()
        channel, message = mock_publish.call_args.args
        assert channel == "channel:dashboard"
        assert message["event"] == "source_disabled"

    # ── runtime scheduling events ────────────────────────────────
    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_enable_publishes_full_source_payload(self, mock_publish):
        """The worker rebuilds the collection job purely from this payload, so it must
        carry every field add_source_job needs (no DB round-trip)."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1", is_active=False, refresh_interval_seconds=120)

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar_one_or_none.return_value = src
            mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        service = SourceService(db, redis)
        await service.update_source(str(src.id), SourceUpdate(is_active=True), "tenant-1")

        mock_publish.assert_awaited_once()
        channel, message = mock_publish.call_args.args
        assert channel == "channel:dashboard"
        assert message["event"] == "source_enabled"
        assert message["source_id"] == str(src.id)
        payload = message["source"]
        assert payload == {
            "id": str(src.id),
            "tenant_id": str(src.tenant_id),
            "category_id": str(src.category_id),
            "category_slug": src.category.slug,
            "name": src.name,
            "source_type": src.source_type,
            "url": src.url,
            "config": src.config,
            "refresh_interval_seconds": 120,
            "is_active": True,
            "priority": src.priority,
        }

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_disable_publishes_source_disabled(self, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1", is_active=True)

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar_one_or_none.return_value = src
            mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        service = SourceService(db, redis)
        await service.update_source(str(src.id), SourceUpdate(is_active=False), "tenant-1")

        mock_publish.assert_awaited_once()
        channel, message = mock_publish.call_args.args
        assert channel == "channel:dashboard"
        assert message["event"] == "source_disabled"
        assert message["source_id"] == str(src.id)
        assert message["source"]["is_active"] is False

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    async def test_update_without_is_active_change_publishes_nothing(self, mock_publish):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1", is_active=True)

        async def execute_side_effect(*args, **kwargs):
            mock_r = MagicMock()
            mock_r.scalar_one_or_none.return_value = src
            mock_r.scalar_one.return_value = src
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.source import SourceUpdate

        service = SourceService(db, redis)
        await service.update_source(str(src.id), SourceUpdate(name="Renamed"), "tenant-1")
        mock_publish.assert_not_called()


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

    @patch("app.services.source.redis_publish", new_callable=AsyncMock)
    @patch("app.services.source.redis_delete", new_callable=AsyncMock)
    async def test_delete_publishes_source_deleted_event(self, mock_del, mock_publish):
        """The scheduler worker removes the collection job based on this event, so lock
        the publisher-side contract against the worker's 'source_deleted' handler."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        src = _make_source(tenant_id="tenant-1")
        mock_result.scalar_one_or_none.return_value = src

        service = SourceService(db, redis)
        await service.delete_source(str(src.id), "tenant-1")

        mock_publish.assert_awaited_once()
        channel, message = mock_publish.call_args.args
        assert channel == "channel:dashboard"
        assert message["event"] == "source_deleted"
        assert message["source_id"] == str(src.id)

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
