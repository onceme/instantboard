import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import CategoryNotFound, DuplicateCategory, Forbidden, ValidationError
from app.services.category import SYSTEM_TENANT_ID, CategoryService, _category_to_response, _slugify


def _make_category(
    cat_id=None,
    tenant_id="tenant-1",
    name="Test Cat",
    slug="test-cat",
    description="desc",
    icon="folder",
    color="#3B82F6",
    type_="custom",
    refresh_interval_seconds=300,
    is_active=True,
    keywords_filter=None,
    created_at=None,
    updated_at=None,
):
    cat = MagicMock()
    cat.id = cat_id or uuid.uuid4()
    cat.tenant_id = tenant_id
    cat.name = name
    cat.slug = slug
    cat.description = description
    cat.icon = icon
    cat.color = color
    cat.type = type_
    cat.refresh_interval_seconds = refresh_interval_seconds
    cat.is_active = is_active
    cat.keywords_filter = keywords_filter or []
    cat.created_at = created_at or datetime.now(UTC)
    cat.updated_at = updated_at or datetime.now(UTC)
    return cat


def _make_tenant(tenant_id=None, max_categories=10):
    tenant = MagicMock()
    tenant.id = tenant_id or uuid.uuid4()
    tenant.max_categories = max_categories
    return tenant


def _mock_db():
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.flush = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    return db, mock_result


def _mock_redis():
    return AsyncMock()


class TestSlugify:
    def test_basic(self):
        assert _slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert _slugify("Test!@#$%Cat") == "testcat"

    def test_multiple_spaces(self):
        assert _slugify("a   b   c") == "a-b-c"

    def test_leading_trailing_dashes(self):
        assert _slugify(" -hello- ") == "hello"

    def test_multiple_dashes(self):
        assert _slugify("a---b") == "a-b"


class TestCategoryToResponse:
    def test_basic_conversion(self):
        cat = _make_category()
        resp = _category_to_response(cat, source_count=5)
        assert resp.id == str(cat.id)
        assert resp.source_count == 5

    def test_defaults(self):
        cat = _make_category(icon=None, color=None)
        resp = _category_to_response(cat)
        assert resp.icon == "folder"
        assert resp.color == "#3B82F6"


class TestCategoryToResponseColorOverrides:
    """Tenant-level color_overrides merge into the response only (P2-16)."""

    def test_override_replaces_stored_color(self):
        cat = _make_category(slug="finance", color="#123456")
        resp = _category_to_response(cat, 0, {"finance": "#FF0000"})
        assert resp.color == "#FF0000"
        # The stored row is never rewritten.
        assert cat.color == "#123456"

    def test_no_override_keeps_stored_color(self):
        cat = _make_category(slug="finance", color="#123456")
        assert _category_to_response(cat, 0, None).color == "#123456"
        assert _category_to_response(cat, 0, {}).color == "#123456"

    def test_unmatched_override_keeps_stored_color(self):
        cat = _make_category(slug="tech", color="#123456")
        resp = _category_to_response(cat, 0, {"finance": "#FF0000"})
        assert resp.color == "#123456"

    def test_override_applies_over_default_color_too(self):
        cat = _make_category(slug="finance", color=None)
        resp = _category_to_response(cat, 0, {"finance": "#00FF00"})
        assert resp.color == "#00FF00"


class TestGetColorOverrides:
    async def test_extracts_color_map_from_tenant_settings(self):
        db, _ = _mock_db()
        service = CategoryService(db, _mock_redis())
        with patch(
            "app.services.category.load_tenant_settings",
            new_callable=AsyncMock,
            return_value={"color_overrides": {"finance": "#FF0000"}, "refresh_overrides": {"finance": 60}},
        ):
            overrides = await service._get_color_overrides("tenant-1")
        assert overrides == {"finance": "#FF0000"}

    async def test_settings_read_failure_degrades_to_empty(self):
        """A broken settings read must never break category listing."""
        db, _ = _mock_db()
        service = CategoryService(db, _mock_redis())
        with patch("app.services.category.load_tenant_settings", new_callable=AsyncMock, return_value={}):
            overrides = await service._get_color_overrides("tenant-1")
        assert overrides == {}


class TestListCategoriesColorOverrides:
    async def test_list_merges_overrides_into_responses(self):
        db, mock_result = _mock_db()
        cat = _make_category(slug="finance", color="#123456")
        mock_result.all.return_value = [(cat, 3)]
        mock_result.scalar.return_value = 1

        service = CategoryService(db, _mock_redis())
        with patch.object(service, "_get_color_overrides", new_callable=AsyncMock, return_value={"finance": "#FF0000"}):
            result = await service.list_categories("tenant-1")

        assert result.data[0].color == "#FF0000"


class TestListCategories:
    async def test_list_with_type_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category()
        mock_result.scalar.return_value = 1
        mock_result.all.return_value = [(cat, 3)]

        service = CategoryService(db, redis)
        result = await service.list_categories("tenant-1", type_filter="finance", page=1, page_size=10)

        assert result.success is True
        assert len(result.data) == 1
        assert result.data[0].source_count == 3
        assert result.meta.total == 1

    async def test_list_without_type_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        mock_result.scalar.return_value = 0
        mock_result.all.return_value = []

        service = CategoryService(db, redis)
        result = await service.list_categories("tenant-1")

        assert result.success is True
        assert len(result.data) == 0


class TestGetCategory:
    async def test_get_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")
        mock_result.scalar_one_or_none.return_value = cat
        mock_result.scalar.return_value = 2

        service = CategoryService(db, redis)
        result = await service.get_category(str(cat.id), "tenant-1")

        assert result.success is True
        assert result.data.source_count == 2

    async def test_get_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.get_category("nonexistent", "tenant-1")

    async def test_get_wrong_tenant(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound, match="not accessible"):
            await service.get_category(str(cat.id), "tenant-1")

    async def test_get_system_category_accessible(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = cat
        mock_result.scalar.return_value = 0

        service = CategoryService(db, redis)
        result = await service.get_category(str(cat.id), "tenant-1")
        assert result.success is True


class TestCreateCategory:
    async def test_create_success(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = _make_tenant(tenant_id="tenant-1")

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

        from app.schemas.category import CategoryCreate, CategoryResponse

        data = CategoryCreate(name="My Cat", type="custom")

        mock_response = MagicMock(spec=CategoryResponse)
        mock_response.id = "fake-id"

        with patch("app.services.category._category_to_response", return_value=mock_response):
            service = CategoryService(db, redis)
            result = await service.create_category(data, "tenant-1")

            assert result.success is True
            db.add.assert_called_once()

    async def test_create_tenant_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        from app.schemas.category import CategoryCreate

        data = CategoryCreate(name="Cat", type="custom")

        service = CategoryService(db, redis)
        with pytest.raises(ValidationError, match="Tenant not found"):
            await service.create_category(data, "bad-tenant")

    async def test_create_limit_reached(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = _make_tenant(max_categories=2)

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

        from app.schemas.category import CategoryCreate

        data = CategoryCreate(name="Cat", type="custom")

        service = CategoryService(db, redis)
        with pytest.raises(ValidationError, match="Category limit reached"):
            await service.create_category(data, "tenant-1")

    async def test_create_duplicate_slug(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant = _make_tenant()
        existing_cat = _make_category(slug="my-cat")

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
                mock_r.scalar_one_or_none.return_value = existing_cat
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.category import CategoryCreate

        data = CategoryCreate(name="My Cat", slug="my-cat", type="custom")

        service = CategoryService(db, redis)
        with pytest.raises(DuplicateCategory, match="already exists"):
            await service.create_category(data, "tenant-1")


class TestUpdateCategory:
    async def test_update_success(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.category import CategoryUpdate

        data = CategoryUpdate(name="Updated Cat")

        service = CategoryService(db, redis)
        result = await service.update_category(str(cat.id), data, "tenant-1")
        assert result.success is True

    async def test_update_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        from app.schemas.category import CategoryUpdate

        data = CategoryUpdate(name="Updated")

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.update_category("nonexistent", data, "tenant-1")

    async def test_update_system_category_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = cat

        from app.schemas.category import CategoryUpdate

        data = CategoryUpdate(name="Updated")

        service = CategoryService(db, redis)
        with pytest.raises(Forbidden, match="Cannot modify predefined"):
            await service.update_category(str(cat.id), data, "tenant-1")

    async def test_update_other_tenant_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = cat

        from app.schemas.category import CategoryUpdate

        data = CategoryUpdate(name="Updated")

        service = CategoryService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.update_category(str(cat.id), data, "tenant-1")

    async def test_update_duplicate_slug(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")
        other_cat = _make_category(slug="taken-slug")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar_one_or_none.return_value = other_cat
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.category import CategoryUpdate

        data = CategoryUpdate(slug="taken-slug")

        service = CategoryService(db, redis)
        with pytest.raises(DuplicateCategory):
            await service.update_category(str(cat.id), data, "tenant-1")

    async def test_update_with_keywords_filter(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.category import CategoryUpdate

        data = CategoryUpdate(keywords_filter=["test", "keyword"])

        service = CategoryService(db, redis)
        result = await service.update_category(str(cat.id), data, "tenant-1")
        assert result.success is True

    async def test_update_owner_tenant_with_uuid_attributes(self):
        """Regression: ORM rows carry tenant_id as uuid.UUID while the JWT tenant id
        is a str. The owner comparison must normalize, otherwise every update of a
        custom category 403s."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        owner_id = uuid.uuid4()
        cat = _make_category(tenant_id=owner_id)

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.scalar.return_value = 0
            return mock_r

        db.execute = execute_side_effect

        from app.schemas.category import CategoryUpdate

        service = CategoryService(db, redis)
        result = await service.update_category(str(cat.id), CategoryUpdate(name="Renamed"), str(owner_id))
        assert result.success is True
        assert cat.name == "Renamed"

    async def test_update_cross_tenant_with_uuid_attributes_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id=uuid.uuid4())
        mock_result.scalar_one_or_none.return_value = cat

        from app.schemas.category import CategoryUpdate

        service = CategoryService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.update_category(str(cat.id), CategoryUpdate(name="Hacked"), str(uuid.uuid4()))


class TestDeleteCategory:
    async def test_delete_success(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")
        mock_result.scalar_one_or_none.return_value = cat
        mock_result.scalar.return_value = 0

        service = CategoryService(db, redis)
        await service.delete_category(str(cat.id), "tenant-1")
        db.delete.assert_called_once_with(cat)

    async def test_delete_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.delete_category("nonexistent", "tenant-1")

    async def test_delete_system_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(Forbidden, match="system categories"):
            await service.delete_category(str(cat.id), "tenant-1")

    async def test_delete_other_tenant_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.delete_category(str(cat.id), "tenant-1")

    async def test_delete_has_sources(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")
        mock_result.scalar_one_or_none.return_value = cat
        mock_result.scalar.return_value = 3

        service = CategoryService(db, redis)
        with pytest.raises(ValidationError, match="active sources"):
            await service.delete_category(str(cat.id), "tenant-1")

    async def test_delete_owner_tenant_with_uuid_attributes(self):
        """Regression: UUID ORM attribute vs str JWT tenant id — the owner delete must
        succeed for the owning tenant."""
        db, mock_result = _mock_db()
        redis = _mock_redis()

        owner_id = uuid.uuid4()
        cat = _make_category(tenant_id=owner_id)
        mock_result.scalar_one_or_none.return_value = cat
        mock_result.scalar.return_value = 0

        service = CategoryService(db, redis)
        await service.delete_category(str(cat.id), str(owner_id))
        db.delete.assert_called_once_with(cat)

    async def test_delete_cross_tenant_with_uuid_attributes_forbidden(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id=uuid.uuid4())
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(Forbidden, match="other tenants"):
            await service.delete_category(str(cat.id), str(uuid.uuid4()))
        db.delete.assert_not_called()


class TestGetPredefinedCategories:
    async def test_returns_system_categories(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat1 = _make_category(tenant_id=SYSTEM_TENANT_ID, name="Finance")
        cat2 = _make_category(tenant_id=SYSTEM_TENANT_ID, name="Tech")

        scalars_mock = MagicMock()
        scalars_mock.all.return_value = [cat1, cat2]
        mock_result.scalars.return_value = scalars_mock
        mock_result.scalar.return_value = 0

        service = CategoryService(db, redis)
        result = await service.get_predefined_categories("tenant-1")

        assert result.success is True
        assert len(result.data) == 2


class TestGetCategoryWithSources:
    async def test_found_with_sources(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")

        source_mock = MagicMock()
        source_mock.id = uuid.uuid4()
        source_mock.name = "RSS Feed"
        source_mock.source_type = "rss"
        source_mock.url = "https://example.com/rss"
        source_mock.is_active = True
        source_mock.priority = 5
        source_mock.refresh_interval_seconds = 300
        health_mock = MagicMock()
        health_mock.status = "healthy"
        source_mock.health = health_mock

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                scalars = MagicMock()
                scalars.all.return_value = [source_mock]
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.get_category_with_sources(str(cat.id), "tenant-1")

        assert result.success is True
        assert result.data.source_count == 1
        assert result.data.sources[0]["health_status"] == "healthy"

    async def test_found_without_health(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1")

        source_mock = MagicMock()
        source_mock.id = uuid.uuid4()
        source_mock.name = "API"
        source_mock.source_type = "api"
        source_mock.url = "https://example.com"
        source_mock.is_active = True
        source_mock.priority = 5
        source_mock.refresh_interval_seconds = 300
        source_mock.health = None

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                scalars = MagicMock()
                scalars.all.return_value = [source_mock]
                mock_r.scalars.return_value = scalars
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.get_category_with_sources(str(cat.id), "tenant-1")
        assert result.data.sources[0]["health_status"] is None

    async def test_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.get_category_with_sources("bad-id", "tenant-1")

    async def test_wrong_tenant(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound, match="not accessible"):
            await service.get_category_with_sources(str(cat.id), "tenant-1")


class TestListSubcategories:
    async def test_finance_with_l2_tags(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="finance")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.all.return_value = [
                    ("china-stock", 10),
                    ("watchlist", 5),
                    ("finance", 3),
                    ("random-tag", 2),
                ]
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.list_subcategories(str(cat.id), "tenant-1")

        assert result.success is True
        tags = [s.tag for s in result.data]
        assert "china-stock" in tags
        assert "watchlist" in tags
        assert "finance" not in tags
        assert "random-tag" not in tags

    async def test_tech_with_l2_tags(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="tech")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.all.return_value = [
                    ("humanoid", 10),
                    ("llm", 8),
                    ("tech", 3),
                    ("robotics", 2),
                ]
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.list_subcategories(str(cat.id), "tenant-1")

        tags = [s.tag for s in result.data]
        assert "humanoid" in tags
        assert "llm" in tags
        assert "tech" not in tags
        assert "robotics" not in tags

    async def test_generic_category_all_tags(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="custom-news")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.all.return_value = [
                    ("news-tag", 5),
                    ("custom-news", 3),
                ]
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.list_subcategories(str(cat.id), "tenant-1")

        tags = [s.tag for s in result.data]
        assert "news-tag" in tags
        assert "custom-news" not in tags

    async def test_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.list_subcategories("bad-id", "tenant-1")

    async def test_wrong_tenant(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        cat = _make_category(tenant_id="other-tenant")
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound, match="not accessible"):
            await service.list_subcategories(str(cat.id), "tenant-1")

    async def test_label_mapping(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="finance")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.all.return_value = [
                    ("china-stock", 10),
                ]
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.list_subcategories(str(cat.id), "tenant-1")

        assert result.data[0].label == "A股行情"

    async def test_deduplication(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="finance")

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_r = MagicMock()
            if call_count == 1:
                mock_r.scalar_one_or_none.return_value = cat
            elif call_count == 2:
                mock_r.all.return_value = [("china-stock", 10), ("china-stock", 5)]
            return mock_r

        db.execute = execute_side_effect

        service = CategoryService(db, redis)
        result = await service.list_subcategories(str(cat.id), "tenant-1")
        china_stock_count = sum(1 for s in result.data if s.tag == "china-stock")
        assert china_stock_count == 1


class TestListCategoryItems:
    async def test_missing_category_raises_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.list_category_items(str(uuid.uuid4()), "tenant-1")

    async def test_cross_tenant_raises_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id=uuid.uuid4())
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound) as exc_info:
            await service.list_category_items(str(cat.id), "other-tenant")
        assert "not accessible" in str(exc_info.value.error_message)

    async def test_own_tenant_delegates_to_tech_service(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        tenant_id = str(uuid.uuid4())
        cat = _make_category(tenant_id=tenant_id)
        mock_result.scalar_one_or_none.return_value = cat

        envelope = {"data": [], "meta": {"total": 0, "page": 2, "page_size": 7}}
        with patch("app.services.category.TechService") as mock_tech_cls:
            mock_tech_cls.return_value.list_category_items = AsyncMock(return_value=envelope)

            service = CategoryService(db, redis)
            result = await service.list_category_items(
                str(cat.id),
                tenant_id,
                sort="time",
                page=2,
                page_size=7,
                since="2026-01-01T00:00:00Z",
            )

        assert result == envelope
        mock_tech_cls.assert_called_once_with(db=db, redis=redis)
        mock_tech_cls.return_value.list_category_items.assert_awaited_once_with(
            category_id=cat.id,
            tenant_id=tenant_id,
            sort="time",
            page=2,
            page_size=7,
            since="2026-01-01T00:00:00Z",
        )

    async def test_system_tenant_category_is_accessible(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id=SYSTEM_TENANT_ID)
        mock_result.scalar_one_or_none.return_value = cat

        envelope = {"data": [], "meta": {"total": 0, "page": 1, "page_size": 20}}
        with patch("app.services.category.TechService") as mock_tech_cls:
            mock_tech_cls.return_value.list_category_items = AsyncMock(return_value=envelope)

            service = CategoryService(db, redis)
            result = await service.list_category_items(str(cat.id), "tenant-1")

        assert result == envelope
        mock_tech_cls.return_value.list_category_items.assert_awaited_once()


def _make_item(title="GPT news", summary="", topic_tags=None, extra_data=None):
    item = MagicMock()
    item.id = uuid.uuid4()
    item.title = title
    item.summary = summary
    item.topic_tags = topic_tags if topic_tags is not None else []
    item.extra_data = dict(extra_data) if extra_data is not None else {}
    return item


class TestReclassifyCategoryItems:
    @staticmethod
    def _execute_sequence(category, batches):
        """First execute fetches the category; each following call pops the next batch.
        The wrapper exposes .calls so tests can assert the number of queries issued."""
        state = {"calls": 0}

        async def execute_side_effect(*args, **kwargs):
            state["calls"] += 1
            mock_r = MagicMock()
            if state["calls"] == 1:
                mock_r.scalar_one_or_none.return_value = category
            else:
                scalars = MagicMock()
                scalars.all.return_value = batches[state["calls"] - 2] if state["calls"] - 2 < len(batches) else []
                mock_r.scalars.return_value = scalars
            return mock_r

        execute_side_effect.calls = state
        return execute_side_effect

    async def test_tech_recompute_overwrites_stale_tags(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="tech", type_="tech")
        item = _make_item(title="GPT update", topic_tags=["tech", "obsolete-tag"])
        db.execute = self._execute_sequence(cat, [[item]])

        service = CategoryService(db, redis)
        result = await service.reclassify_category_items(str(cat.id), "tenant-1")

        assert result.success is True
        assert result.data.scanned == 1
        assert result.data.updated == 1
        assert item.topic_tags == ["tech", "ai", "llm"]

    async def test_unchanged_tags_skip_write(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="tech", type_="tech")
        original_tags = ["tech", "ai", "llm"]
        item = _make_item(title="GPT update", topic_tags=original_tags)
        db.execute = self._execute_sequence(cat, [[item]])

        service = CategoryService(db, redis)
        result = await service.reclassify_category_items(str(cat.id), "tenant-1")

        assert result.data.scanned == 1
        assert result.data.updated == 0
        # Identity check: the attribute was never reassigned, so SQLAlchemy has
        # nothing to flush for this row (no spurious UPDATE)
        assert item.topic_tags is original_tags

    async def test_finance_rules_reused(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="finance", type_="finance")
        item = _make_item(title="黄金价格创新高", topic_tags=["finance", "china-stock"])
        db.execute = self._execute_sequence(cat, [[item]])

        service = CategoryService(db, redis)
        result = await service.reclassify_category_items(str(cat.id), "tenant-1")

        assert result.data.updated == 1
        assert item.topic_tags == ["finance", "commodities"]

    async def test_custom_category_falls_back_to_slug(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="sports", type_="custom")
        item = _make_item(title="Match tonight", topic_tags=["stale"])
        db.execute = self._execute_sequence(cat, [[item]])

        service = CategoryService(db, redis)
        result = await service.reclassify_category_items(str(cat.id), "tenant-1")

        assert result.data.updated == 1
        assert item.topic_tags == ["sports"]

    async def test_batching_scans_every_item(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id="tenant-1", slug="tech", type_="tech")
        items = [_make_item(title=f"GPT item {i}", topic_tags=["stale"]) for i in range(3)]
        execute_seq = self._execute_sequence(cat, [items[:2], items[2:]])
        db.execute = execute_seq

        service = CategoryService(db, redis)
        result = await service.reclassify_category_items(str(cat.id), "tenant-1", batch_size=2)

        assert result.data.scanned == 3
        assert result.data.updated == 3
        for item in items:
            assert item.topic_tags == ["tech", "ai", "llm"]
        # 1 category fetch + 2 batch queries (stops at the short batch, no empty probe)
        assert execute_seq.calls["calls"] == 3
        assert db.flush.await_count == 2

    async def test_missing_category_raises_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()
        mock_result.scalar_one_or_none.return_value = None

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound):
            await service.reclassify_category_items(str(uuid.uuid4()), "tenant-1")

    async def test_cross_tenant_raises_not_found(self):
        db, mock_result = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id=uuid.uuid4())
        mock_result.scalar_one_or_none.return_value = cat

        service = CategoryService(db, redis)
        with pytest.raises(CategoryNotFound, match="not accessible"):
            await service.reclassify_category_items(str(cat.id), "tenant-1")
        db.flush.assert_not_awaited()

    async def test_system_category_is_accessible(self):
        db, _ = _mock_db()
        redis = _mock_redis()

        cat = _make_category(tenant_id=SYSTEM_TENANT_ID, slug="tech", type_="tech")
        item = _make_item(title="Starlink launch", topic_tags=["stale"])
        db.execute = self._execute_sequence(cat, [[item]])

        service = CategoryService(db, redis)
        result = await service.reclassify_category_items(str(cat.id), "tenant-1")

        assert result.success is True
        assert result.data.scanned == 1
        assert item.topic_tags == ["tech", "space", "satellite-internet"]
