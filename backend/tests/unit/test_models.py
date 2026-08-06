import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import RelationshipProperty

from app.models.base import Base, BaseModel, TenantMixin, TimestampMixin
from app.models.category import Category
from app.models.dashboard import DashboardSnapshot
from app.models.finance import FinanceQuote, FinanceSymbol, FundNAVEstimate
from app.models.item import Item
from app.models.source import Source, SourceHealth
from app.models.sse import SSEConnection
from app.models.tenant import Tenant
from app.models.user import User
from app.models.watchlist import WatchlistItem


def _get_column(model_class, name):
    return model_class.__table__.columns[name]


def _get_relationship(model_class, name) -> RelationshipProperty:
    return model_class.__mapper__.relationships[name]


def _get_unique_constraints(model_class):
    return [c for c in model_class.__table__.constraints if isinstance(c, UniqueConstraint)]


def _get_check_constraints(model_class):
    return [c for c in model_class.__table__.constraints if isinstance(c, CheckConstraint)]


def _get_indexes(model_class):
    return list(model_class.__table__.indexes)


def _default_fn_name(col):
    arg = col.default.arg if col.default is not None else None
    if arg is None:
        return None
    if isinstance(arg, type):
        return arg.__name__
    if callable(arg):
        return getattr(arg, "__name__", None)
    return arg


class TestBaseClass:
    def test_base_is_declarative(self):
        assert hasattr(Base, "metadata")
        assert hasattr(Base, "registry")

    def test_timestamp_mixin_created_at(self):
        assert hasattr(TimestampMixin, "created_at")

    def test_timestamp_mixin_updated_at(self):
        assert hasattr(TimestampMixin, "updated_at")

    def test_tenant_mixin_tenant_id(self):
        assert hasattr(TenantMixin, "tenant_id")

    def test_base_model_is_abstract(self):
        assert BaseModel.__abstract__ is True

    def test_base_model_inherits_base(self):
        assert issubclass(BaseModel, Base)


class TestTenant:
    def test_tablename(self):
        assert Tenant.__tablename__ == "tenants"

    def test_inherits_base_model(self):
        assert issubclass(Tenant, BaseModel)

    def test_id_column(self):
        col = _get_column(Tenant, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_created_at_column(self):
        col = _get_column(Tenant, "created_at")
        assert col.nullable is False

    def test_updated_at_column(self):
        col = _get_column(Tenant, "updated_at")
        assert col.nullable is False

    def test_name_column(self):
        col = _get_column(Tenant, "name")
        assert isinstance(col.type, String)
        assert col.type.length == 100
        assert col.nullable is False

    def test_slug_column(self):
        col = _get_column(Tenant, "slug")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.nullable is False
        assert col.unique is True

    def test_plan_column(self):
        col = _get_column(Tenant, "plan")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False
        assert col.default.arg == "free"

    def test_settings_column_jsonb(self):
        col = _get_column(Tenant, "settings")
        assert isinstance(col.type, JSONB)
        assert col.nullable is False
        assert _default_fn_name(col) == "dict"

    def test_max_users_column(self):
        col = _get_column(Tenant, "max_users")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 5

    def test_max_categories_column(self):
        col = _get_column(Tenant, "max_categories")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 10

    def test_max_sources_column(self):
        col = _get_column(Tenant, "max_sources")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 50

    def test_is_active_column(self):
        col = _get_column(Tenant, "is_active")
        assert isinstance(col.type, Boolean)
        assert col.nullable is False
        assert col.default.arg is True

    def test_check_constraint_plan_values(self):
        checks = _get_check_constraints(Tenant)
        plan_checks = [c for c in checks if c.name == "chk_tenants_plan"]
        assert len(plan_checks) == 1

    def test_relationships(self):
        users_rel = _get_relationship(Tenant, "users")
        assert users_rel.direction.name == "ONETOMANY"
        assert users_rel.back_populates == "tenant"
        assert "delete-orphan" in str(users_rel.cascade)
        assert users_rel.lazy == "noload"

        cats_rel = _get_relationship(Tenant, "categories")
        assert cats_rel.direction.name == "ONETOMANY"
        assert cats_rel.back_populates == "tenant"

        sources_rel = _get_relationship(Tenant, "sources")
        assert sources_rel.direction.name == "ONETOMANY"
        assert sources_rel.back_populates == "tenant"

    def test_instantiation(self):
        tenant = Tenant(
            name="Test Tenant", slug="test-tenant", plan="free",
            settings={}, is_active=True, max_users=5, max_categories=10, max_sources=50,
        )
        assert tenant.name == "Test Tenant"
        assert tenant.slug == "test-tenant"
        assert tenant.plan == "free"
        assert tenant.is_active is True
        assert tenant.settings == {}
        assert tenant.max_users == 5

    def test_repr(self):
        tenant = Tenant(name="Foo", slug="foo")
        r = repr(tenant)
        assert "Tenant" in r


class TestUser:
    def test_tablename(self):
        assert User.__tablename__ == "users"

    def test_inherits_base_model(self):
        assert issubclass(User, BaseModel)

    def test_id_column(self):
        col = _get_column(User, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)

    def test_tenant_id_column(self):
        col = _get_column(User, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"
        assert fk.ondelete == "CASCADE"

    def test_email_column(self):
        col = _get_column(User, "email")
        assert isinstance(col.type, String)
        assert col.type.length == 255
        assert col.nullable is False

    def test_name_column(self):
        col = _get_column(User, "name")
        assert isinstance(col.type, String)
        assert col.type.length == 100
        assert col.nullable is False

    def test_avatar_url_column(self):
        col = _get_column(User, "avatar_url")
        assert isinstance(col.type, Text)
        assert col.nullable is True

    def test_sso_provider_column(self):
        col = _get_column(User, "sso_provider")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False

    def test_sso_provider_id_column(self):
        col = _get_column(User, "sso_provider_id")
        assert isinstance(col.type, String)
        assert col.type.length == 255
        assert col.nullable is False

    def test_role_column(self):
        col = _get_column(User, "role")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False
        assert col.default.arg == "member"

    def test_preferences_column(self):
        col = _get_column(User, "preferences")
        assert isinstance(col.type, JSONB)
        assert col.nullable is False
        assert _default_fn_name(col) == "dict"

    def test_last_login_at_column(self):
        col = _get_column(User, "last_login_at")
        assert col.nullable is True

    def test_unique_constraints(self):
        ucs = _get_unique_constraints(User)
        names = {c.name for c in ucs}
        assert "uq_users_tenant_email" in names
        assert "uq_users_sso" in names

    def test_check_constraints(self):
        checks = _get_check_constraints(User)
        check_names = {c.name for c in checks}
        assert "chk_users_sso_provider" in check_names
        assert "chk_users_role" in check_names

    def test_indexes(self):
        indexes = _get_indexes(User)
        idx_names = {i.name for i in indexes}
        assert "idx_users_tenant" in idx_names
        assert "idx_users_sso" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(User, "tenant")
        assert tenant_rel.back_populates == "users"
        assert tenant_rel.lazy == "selectin"

        watchlist_rel = _get_relationship(User, "watchlist_items")
        assert watchlist_rel.back_populates == "user"
        assert "delete-orphan" in str(watchlist_rel.cascade)

        sse_rel = _get_relationship(User, "sse_connections")
        assert sse_rel.back_populates == "user"
        assert "delete-orphan" in str(sse_rel.cascade)


class TestCategory:
    def test_tablename(self):
        assert Category.__tablename__ == "categories"

    def test_inherits_base_model(self):
        assert issubclass(Category, BaseModel)

    def test_tenant_id_column(self):
        col = _get_column(Category, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_name_column(self):
        col = _get_column(Category, "name")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.nullable is False

    def test_slug_column(self):
        col = _get_column(Category, "slug")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.nullable is False

    def test_description_column(self):
        col = _get_column(Category, "description")
        assert isinstance(col.type, String)
        assert col.type.length == 200
        assert col.nullable is True

    def test_icon_column(self):
        col = _get_column(Category, "icon")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.default.arg == "folder"

    def test_color_column(self):
        col = _get_column(Category, "color")
        assert isinstance(col.type, String)
        assert col.type.length == 7
        assert col.default is not None

    def test_type_column(self):
        col = _get_column(Category, "type")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False

    def test_refresh_interval_seconds_column(self):
        col = _get_column(Category, "refresh_interval_seconds")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 300

    def test_keywords_filter_column(self):
        col = _get_column(Category, "keywords_filter")
        assert isinstance(col.type, JSONB)
        assert _default_fn_name(col) == "list"

    def test_priority_sort_column(self):
        col = _get_column(Category, "priority_sort")
        assert isinstance(col.type, Boolean)
        assert col.nullable is False
        assert col.default.arg is False

    def test_is_active_column(self):
        col = _get_column(Category, "is_active")
        assert isinstance(col.type, Boolean)
        assert col.nullable is False
        assert col.default.arg is True

    def test_unique_constraints(self):
        ucs = _get_unique_constraints(Category)
        names = {c.name for c in ucs}
        assert "uq_categories_tenant_slug" in names

    def test_check_constraints(self):
        checks = _get_check_constraints(Category)
        names = {c.name for c in checks}
        assert "chk_categories_type" in names

    def test_indexes(self):
        indexes = _get_indexes(Category)
        idx_names = {i.name for i in indexes}
        assert "idx_categories_tenant" in idx_names
        assert "idx_categories_type" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(Category, "tenant")
        assert tenant_rel.back_populates == "categories"
        assert tenant_rel.lazy == "selectin"

        sources_rel = _get_relationship(Category, "sources")
        assert sources_rel.back_populates == "category"
        assert "delete-orphan" in str(sources_rel.cascade)

        items_rel = _get_relationship(Category, "items")
        assert items_rel.back_populates == "category"
        assert "delete-orphan" in str(items_rel.cascade)


class TestSource:
    def test_tablename(self):
        assert Source.__tablename__ == "sources"

    def test_inherits_base_model(self):
        assert issubclass(Source, BaseModel)

    def test_tenant_id_column(self):
        col = _get_column(Source, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_category_id_column(self):
        col = _get_column(Source, "category_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "categories.id"

    def test_name_column(self):
        col = _get_column(Source, "name")
        assert isinstance(col.type, String)
        assert col.type.length == 100
        assert col.nullable is False

    def test_source_type_column(self):
        col = _get_column(Source, "source_type")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False

    def test_url_column(self):
        col = _get_column(Source, "url")
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_config_column(self):
        col = _get_column(Source, "config")
        assert isinstance(col.type, JSONB)
        assert col.nullable is False
        assert _default_fn_name(col) == "dict"

    def test_refresh_interval_seconds_column(self):
        col = _get_column(Source, "refresh_interval_seconds")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_is_active_column(self):
        col = _get_column(Source, "is_active")
        assert isinstance(col.type, Boolean)
        assert col.nullable is False
        assert col.default.arg is True

    def test_priority_column(self):
        col = _get_column(Source, "priority")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 5

    def test_check_constraints(self):
        checks = _get_check_constraints(Source)
        names = {c.name for c in checks}
        assert "chk_sources_type" in names

    def test_indexes(self):
        indexes = _get_indexes(Source)
        idx_names = {i.name for i in indexes}
        assert "idx_sources_category" in idx_names
        assert "idx_sources_tenant" in idx_names
        assert "idx_sources_type" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(Source, "tenant")
        assert tenant_rel.back_populates == "sources"
        assert tenant_rel.lazy == "selectin"

        cat_rel = _get_relationship(Source, "category")
        assert cat_rel.back_populates == "sources"
        assert cat_rel.lazy == "selectin"

        health_rel = _get_relationship(Source, "health")
        assert health_rel.back_populates == "source"
        assert health_rel.uselist is False
        assert "delete-orphan" in str(health_rel.cascade)

        items_rel = _get_relationship(Source, "items")
        assert items_rel.back_populates == "source"
        assert "delete-orphan" in str(items_rel.cascade)


class TestSourceHealth:
    def test_tablename(self):
        assert SourceHealth.__tablename__ == "source_health"

    def test_inherits_base(self):
        assert issubclass(SourceHealth, Base)
        assert not issubclass(SourceHealth, BaseModel)

    def test_id_column(self):
        col = _get_column(SourceHealth, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_source_id_column(self):
        col = _get_column(SourceHealth, "source_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        assert col.unique is True
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "sources.id"

    def test_status_column(self):
        col = _get_column(SourceHealth, "status")
        assert isinstance(col.type, String)
        assert col.type.length == 10
        assert col.nullable is False
        assert col.default.arg == "healthy"

    def test_last_success_at_column(self):
        col = _get_column(SourceHealth, "last_success_at")
        assert col.nullable is True

    def test_last_failure_at_column(self):
        col = _get_column(SourceHealth, "last_failure_at")
        assert col.nullable is True

    def test_last_error_message_column(self):
        col = _get_column(SourceHealth, "last_error_message")
        assert isinstance(col.type, Text)
        assert col.nullable is True

    def test_consecutive_failures_column(self):
        col = _get_column(SourceHealth, "consecutive_failures")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 0

    def test_total_fetches_24h_column(self):
        col = _get_column(SourceHealth, "total_fetches_24h")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 0

    def test_success_count_24h_column(self):
        col = _get_column(SourceHealth, "success_count_24h")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 0

    def test_avg_response_time_ms_column(self):
        col = _get_column(SourceHealth, "avg_response_time_ms")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 0

    def test_updated_at_column(self):
        col = _get_column(SourceHealth, "updated_at")
        assert col.nullable is False

    def test_check_constraints(self):
        checks = _get_check_constraints(SourceHealth)
        names = {c.name for c in checks}
        assert "chk_source_health_status" in names

    def test_indexes(self):
        indexes = _get_indexes(SourceHealth)
        idx_names = {i.name for i in indexes}
        assert "idx_source_health_status" in idx_names

    def test_source_relationship(self):
        rel = _get_relationship(SourceHealth, "source")
        assert rel.back_populates == "health"
        assert rel.lazy == "selectin"


class TestItem:
    def test_tablename(self):
        assert Item.__tablename__ == "items"

    def test_inherits_base(self):
        assert issubclass(Item, Base)
        assert not issubclass(Item, BaseModel)

    def test_id_column(self):
        col = _get_column(Item, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_tenant_id_column(self):
        col = _get_column(Item, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_category_id_column(self):
        col = _get_column(Item, "category_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "categories.id"

    def test_source_id_column(self):
        col = _get_column(Item, "source_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "sources.id"

    def test_title_column(self):
        col = _get_column(Item, "title")
        assert isinstance(col.type, String)
        assert col.type.length == 500
        assert col.nullable is False

    def test_summary_column(self):
        col = _get_column(Item, "summary")
        assert isinstance(col.type, Text)
        assert col.nullable is True

    def test_url_column(self):
        col = _get_column(Item, "url")
        assert isinstance(col.type, Text)
        assert col.nullable is False

    def test_image_url_column(self):
        col = _get_column(Item, "image_url")
        assert isinstance(col.type, Text)
        assert col.nullable is True

    def test_published_at_column(self):
        col = _get_column(Item, "published_at")
        assert col.nullable is False

    def test_topic_tags_column(self):
        col = _get_column(Item, "topic_tags")
        assert isinstance(col.type, JSONB)
        assert _default_fn_name(col) == "list"

    def test_extra_data_column(self):
        col = _get_column(Item, "extra_data")
        assert isinstance(col.type, JSONB)
        assert _default_fn_name(col) == "dict"

    def test_priority_column(self):
        col = _get_column(Item, "priority")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 5

    def test_is_processed_column(self):
        col = _get_column(Item, "is_processed")
        assert isinstance(col.type, Boolean)
        assert col.nullable is False
        assert col.default.arg is True

    def test_created_at_column(self):
        col = _get_column(Item, "created_at")
        assert col.nullable is False

    def test_unique_constraints(self):
        ucs = _get_unique_constraints(Item)
        names = {c.name for c in ucs}
        assert "uq_items_dedup" in names

    def test_indexes(self):
        indexes = _get_indexes(Item)
        idx_names = {i.name for i in indexes}
        assert "idx_items_category" in idx_names
        assert "idx_items_tenant_time" in idx_names
        assert "idx_items_source" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(Item, "tenant")
        assert tenant_rel.lazy == "selectin"

        cat_rel = _get_relationship(Item, "category")
        assert cat_rel.back_populates == "items"
        assert cat_rel.lazy == "selectin"

        src_rel = _get_relationship(Item, "source")
        assert src_rel.back_populates == "items"
        assert src_rel.lazy == "selectin"


class TestFinanceSymbol:
    def test_tablename(self):
        assert FinanceSymbol.__tablename__ == "finance_symbols"

    def test_inherits_base_model(self):
        assert issubclass(FinanceSymbol, BaseModel)

    def test_tenant_id_column(self):
        col = _get_column(FinanceSymbol, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_symbol_column(self):
        col = _get_column(FinanceSymbol, "symbol")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False

    def test_name_column(self):
        col = _get_column(FinanceSymbol, "name")
        assert isinstance(col.type, String)
        assert col.type.length == 200
        assert col.nullable is False

    def test_type_column(self):
        col = _get_column(FinanceSymbol, "type")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is False

    def test_market_column(self):
        col = _get_column(FinanceSymbol, "market")
        assert isinstance(col.type, String)
        assert col.type.length == 10
        assert col.nullable is False

    def test_exchange_column(self):
        col = _get_column(FinanceSymbol, "exchange")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.nullable is True

    def test_currency_column(self):
        col = _get_column(FinanceSymbol, "currency")
        assert isinstance(col.type, String)
        assert col.type.length == 3
        assert col.default is not None

    def test_is_active_column(self):
        col = _get_column(FinanceSymbol, "is_active")
        assert isinstance(col.type, Boolean)
        assert col.nullable is False
        assert col.default.arg is True

    def test_unique_constraints(self):
        ucs = _get_unique_constraints(FinanceSymbol)
        names = {c.name for c in ucs}
        assert "uq_finance_symbols_tenant_symbol" in names

    def test_check_constraints(self):
        checks = _get_check_constraints(FinanceSymbol)
        names = {c.name for c in checks}
        assert "chk_finance_symbols_type" in names

    def test_indexes(self):
        indexes = _get_indexes(FinanceSymbol)
        idx_names = {i.name for i in indexes}
        assert "idx_finance_symbols_type" in idx_names
        assert "idx_finance_symbols_market" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(FinanceSymbol, "tenant")
        assert tenant_rel.lazy == "selectin"

        quotes_rel = _get_relationship(FinanceSymbol, "quotes")
        assert quotes_rel.back_populates == "symbol"
        assert "delete-orphan" in str(quotes_rel.cascade)
        assert quotes_rel.lazy == "noload"

        nav_rel = _get_relationship(FinanceSymbol, "nav_estimates")
        assert nav_rel.back_populates == "symbol"
        assert "delete-orphan" in str(nav_rel.cascade)

        watchlist_rel = _get_relationship(FinanceSymbol, "watchlist_items")
        assert watchlist_rel.back_populates == "symbol"
        assert "delete-orphan" in str(watchlist_rel.cascade)


class TestFinanceQuote:
    def test_tablename(self):
        assert FinanceQuote.__tablename__ == "finance_quotes"

    def test_inherits_base(self):
        assert issubclass(FinanceQuote, Base)
        assert not issubclass(FinanceQuote, BaseModel)

    def test_id_column(self):
        col = _get_column(FinanceQuote, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_tenant_id_column(self):
        col = _get_column(FinanceQuote, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_symbol_id_column(self):
        col = _get_column(FinanceQuote, "symbol_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "finance_symbols.id"

    def test_current_price_column(self):
        col = _get_column(FinanceQuote, "current_price")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_open_price_column(self):
        col = _get_column(FinanceQuote, "open_price")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_high_price_column(self):
        col = _get_column(FinanceQuote, "high_price")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_low_price_column(self):
        col = _get_column(FinanceQuote, "low_price")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_close_previous_column(self):
        col = _get_column(FinanceQuote, "close_previous")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_volume_column(self):
        col = _get_column(FinanceQuote, "volume")
        assert col.nullable is True

    def test_change_value_column(self):
        col = _get_column(FinanceQuote, "change_value")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_change_percent_column(self):
        col = _get_column(FinanceQuote, "change_percent")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_market_cap_column(self):
        col = _get_column(FinanceQuote, "market_cap")
        assert col.nullable is True

    def test_pe_ratio_column(self):
        col = _get_column(FinanceQuote, "pe_ratio")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_52_week_high_column(self):
        col = _get_column(FinanceQuote, "52_week_high")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_52_week_low_column(self):
        col = _get_column(FinanceQuote, "52_week_low")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_timestamp_column(self):
        col = _get_column(FinanceQuote, "timestamp")
        assert col.nullable is False

    def test_source_name_column(self):
        col = _get_column(FinanceQuote, "source_name")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.nullable is True

    def test_created_at_column(self):
        col = _get_column(FinanceQuote, "created_at")
        assert col.nullable is False

    def test_indexes(self):
        indexes = _get_indexes(FinanceQuote)
        idx_names = {i.name for i in indexes}
        assert "idx_finance_quotes_symbol_time" in idx_names
        assert "idx_finance_quotes_tenant" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(FinanceQuote, "tenant")
        assert tenant_rel.lazy == "selectin"

        symbol_rel = _get_relationship(FinanceQuote, "symbol")
        assert symbol_rel.back_populates == "quotes"
        assert symbol_rel.lazy == "selectin"


class TestFundNAVEstimate:
    def test_tablename(self):
        assert FundNAVEstimate.__tablename__ == "fund_nav_estimates"

    def test_inherits_base(self):
        assert issubclass(FundNAVEstimate, Base)
        assert not issubclass(FundNAVEstimate, BaseModel)

    def test_id_column(self):
        col = _get_column(FundNAVEstimate, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)

    def test_tenant_id_column(self):
        col = _get_column(FundNAVEstimate, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_symbol_id_column(self):
        col = _get_column(FundNAVEstimate, "symbol_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "finance_symbols.id"

    def test_nav_official_column(self):
        col = _get_column(FundNAVEstimate, "nav_official")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_nav_official_date_column(self):
        col = _get_column(FundNAVEstimate, "nav_official_date")
        assert col.nullable is True

    def test_nav_estimate_column(self):
        col = _get_column(FundNAVEstimate, "nav_estimate")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_nav_estimate_deviation_percent_column(self):
        col = _get_column(FundNAVEstimate, "nav_estimate_deviation_percent")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_estimate_method_column(self):
        col = _get_column(FundNAVEstimate, "estimate_method")
        assert isinstance(col.type, String)
        assert col.type.length == 50
        assert col.nullable is True

    def test_estimate_timestamp_column(self):
        col = _get_column(FundNAVEstimate, "estimate_timestamp")
        assert col.nullable is False

    def test_underlying_index_symbol_column(self):
        col = _get_column(FundNAVEstimate, "underlying_index_symbol")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert col.nullable is True

    def test_underlying_index_value_column(self):
        col = _get_column(FundNAVEstimate, "underlying_index_value")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_underlying_index_change_percent_column(self):
        col = _get_column(FundNAVEstimate, "underlying_index_change_percent")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_created_at_column(self):
        col = _get_column(FundNAVEstimate, "created_at")
        assert col.nullable is False

    def test_indexes(self):
        indexes = _get_indexes(FundNAVEstimate)
        idx_names = {i.name for i in indexes}
        assert "idx_fund_nav_symbol" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(FundNAVEstimate, "tenant")
        assert tenant_rel.lazy == "selectin"

        symbol_rel = _get_relationship(FundNAVEstimate, "symbol")
        assert symbol_rel.back_populates == "nav_estimates"
        assert symbol_rel.lazy == "selectin"


class TestWatchlistItem:
    def test_tablename(self):
        assert WatchlistItem.__tablename__ == "watchlist_items"

    def test_inherits_base(self):
        assert issubclass(WatchlistItem, Base)
        assert not issubclass(WatchlistItem, BaseModel)

    def test_id_column(self):
        col = _get_column(WatchlistItem, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_tenant_id_column(self):
        col = _get_column(WatchlistItem, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_user_id_column(self):
        col = _get_column(WatchlistItem, "user_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "users.id"

    def test_symbol_id_column(self):
        col = _get_column(WatchlistItem, "symbol_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "finance_symbols.id"

    def test_display_order_column(self):
        col = _get_column(WatchlistItem, "display_order")
        assert isinstance(col.type, Integer)
        assert col.nullable is False
        assert col.default.arg == 0

    def test_notes_column(self):
        col = _get_column(WatchlistItem, "notes")
        assert isinstance(col.type, String)
        assert col.type.length == 200
        assert col.nullable is True

    def test_alert_threshold_percent_column(self):
        col = _get_column(WatchlistItem, "alert_threshold_percent")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_created_at_column(self):
        col = _get_column(WatchlistItem, "created_at")
        assert col.nullable is False

    def test_unique_constraints(self):
        ucs = _get_unique_constraints(WatchlistItem)
        names = {c.name for c in ucs}
        assert "uq_watchlist_user_symbol" in names

    def test_indexes(self):
        indexes = _get_indexes(WatchlistItem)
        idx_names = {i.name for i in indexes}
        assert "idx_watchlist_user" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(WatchlistItem, "tenant")
        assert tenant_rel.lazy == "selectin"

        user_rel = _get_relationship(WatchlistItem, "user")
        assert user_rel.back_populates == "watchlist_items"
        assert user_rel.lazy == "selectin"

        symbol_rel = _get_relationship(WatchlistItem, "symbol")
        assert symbol_rel.back_populates == "watchlist_items"
        assert symbol_rel.lazy == "selectin"


class TestSSEConnection:
    def test_tablename(self):
        assert SSEConnection.__tablename__ == "sse_connections"

    def test_inherits_base(self):
        assert issubclass(SSEConnection, Base)
        assert not issubclass(SSEConnection, BaseModel)

    def test_id_column(self):
        col = _get_column(SSEConnection, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_tenant_id_column(self):
        col = _get_column(SSEConnection, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_user_id_column(self):
        col = _get_column(SSEConnection, "user_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "users.id"

    def test_channels_column(self):
        col = _get_column(SSEConnection, "channels")
        assert isinstance(col.type, JSONB)
        assert col.nullable is False
        assert _default_fn_name(col) == "list"

    def test_connected_at_column(self):
        col = _get_column(SSEConnection, "connected_at")
        assert col.nullable is False

    def test_disconnected_at_column(self):
        col = _get_column(SSEConnection, "disconnected_at")
        assert col.nullable is True

    def test_last_heartbeat_at_column(self):
        col = _get_column(SSEConnection, "last_heartbeat_at")
        assert col.nullable is True

    def test_client_ip_column(self):
        col = _get_column(SSEConnection, "client_ip")
        assert isinstance(col.type, String)
        assert col.type.length == 45
        assert col.nullable is True

    def test_user_agent_column(self):
        col = _get_column(SSEConnection, "user_agent")
        assert isinstance(col.type, Text)
        assert col.nullable is True

    def test_indexes(self):
        indexes = _get_indexes(SSEConnection)
        idx_names = {i.name for i in indexes}
        assert "idx_sse_connections_user" in idx_names

    def test_relationships(self):
        tenant_rel = _get_relationship(SSEConnection, "tenant")
        assert tenant_rel.lazy == "selectin"

        user_rel = _get_relationship(SSEConnection, "user")
        assert user_rel.back_populates == "sse_connections"
        assert user_rel.lazy == "selectin"


class TestDashboardSnapshot:
    def test_tablename(self):
        assert DashboardSnapshot.__tablename__ == "dashboard_snapshots"

    def test_inherits_base(self):
        assert issubclass(DashboardSnapshot, Base)
        assert not issubclass(DashboardSnapshot, BaseModel)

    def test_id_column(self):
        col = _get_column(DashboardSnapshot, "id")
        assert col.primary_key is True
        assert isinstance(col.type, UUID)
        assert _default_fn_name(col) == "uuid4"

    def test_tenant_id_column(self):
        col = _get_column(DashboardSnapshot, "tenant_id")
        assert isinstance(col.type, UUID)
        assert col.nullable is False
        fk = list(col.foreign_keys)[0]
        assert str(fk.target_fullname) == "tenants.id"

    def test_cpu_usage_percent_column(self):
        col = _get_column(DashboardSnapshot, "cpu_usage_percent")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_memory_used_mb_column(self):
        col = _get_column(DashboardSnapshot, "memory_used_mb")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_memory_total_mb_column(self):
        col = _get_column(DashboardSnapshot, "memory_total_mb")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_disk_used_gb_column(self):
        col = _get_column(DashboardSnapshot, "disk_used_gb")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_disk_total_gb_column(self):
        col = _get_column(DashboardSnapshot, "disk_total_gb")
        assert isinstance(col.type, Numeric)
        assert col.nullable is True

    def test_active_sse_connections_column(self):
        col = _get_column(DashboardSnapshot, "active_sse_connections")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_events_pushed_hour_column(self):
        col = _get_column(DashboardSnapshot, "events_pushed_hour")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_healthy_sources_column(self):
        col = _get_column(DashboardSnapshot, "healthy_sources")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_degraded_sources_column(self):
        col = _get_column(DashboardSnapshot, "degraded_sources")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_down_sources_column(self):
        col = _get_column(DashboardSnapshot, "down_sources")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_scheduler_jobs_active_column(self):
        col = _get_column(DashboardSnapshot, "scheduler_jobs_active")
        assert isinstance(col.type, Integer)
        assert col.nullable is True

    def test_timestamp_column(self):
        col = _get_column(DashboardSnapshot, "timestamp")
        assert col.nullable is False

    def test_indexes(self):
        indexes = _get_indexes(DashboardSnapshot)
        idx_names = {i.name for i in indexes}
        assert "idx_dashboard_snapshots_time" in idx_names

    def test_relationship(self):
        rel = _get_relationship(DashboardSnapshot, "tenant")
        assert rel.lazy == "selectin"


class TestModelsInitExports:
    def test_all_models_in_all(self):
        expected = {
            "Base", "TimestampMixin", "TenantMixin", "BaseModel",
            "Tenant", "User", "Category", "Source", "SourceHealth",
            "Item", "FinanceSymbol", "FinanceQuote", "FundNAVEstimate",
            "WatchlistItem", "SSEConnection", "DashboardSnapshot",
        }
        from app.models import __all__
        assert set(__all__) == expected

    def test_metadata_tables(self):
        table_names = set(Base.metadata.tables.keys())
        expected = {
            "tenants", "users", "categories", "sources", "source_health",
            "items", "finance_symbols", "finance_quotes", "fund_nav_estimates",
            "watchlist_items", "sse_connections", "dashboard_snapshots",
        }
        assert expected.issubset(table_names)


class TestModelsDatabasePersistAndRetrieve:
    async def test_tenant_crud(self, db_session):
        tenant = Tenant(name="Acme", slug="acme")
        db_session.add(tenant)
        await db_session.commit()
        assert tenant.id is not None
        assert tenant.name == "Acme"
        assert tenant.slug == "acme"
        assert tenant.plan == "free"
        assert isinstance(tenant.created_at, datetime)
        assert isinstance(tenant.updated_at, datetime)

    async def test_user_crud(self, db_session):
        tenant = Tenant(name="U Tenant", slug="u-tenant")
        db_session.add(tenant)
        await db_session.flush()

        user = User(
            tenant_id=tenant.id,
            email="user@example.com",
            name="Test User",
            sso_provider="google",
            sso_provider_id="google-123",
        )
        db_session.add(user)
        await db_session.commit()

        assert user.id is not None
        assert user.role == "member"
        assert user.email == "user@example.com"

    async def test_category_crud(self, db_session):
        tenant = Tenant(name="Cat Tenant", slug="cat-tenant")
        db_session.add(tenant)
        await db_session.flush()

        category = Category(
            tenant_id=tenant.id,
            name="Tech",
            slug="tech",
            type="tech",
        )
        db_session.add(category)
        await db_session.commit()

        assert category.id is not None
        assert category.refresh_interval_seconds == 300
        assert category.is_active is True
        assert category.icon == "folder"
        assert category.priority_sort is False

    async def test_source_and_health_crud(self, db_session):
        tenant = Tenant(name="Src Tenant", slug="src-tenant")
        db_session.add(tenant)
        await db_session.flush()

        category = Category(
            tenant_id=tenant.id, name="News", slug="news", type="news"
        )
        db_session.add(category)
        await db_session.flush()

        source = Source(
            tenant_id=tenant.id,
            category_id=category.id,
            name="TechCrunch",
            source_type="rss",
            url="https://techcrunch.com/feed/",
        )
        db_session.add(source)
        await db_session.flush()

        health = SourceHealth(source_id=source.id)
        db_session.add(health)
        await db_session.commit()

        assert source.priority == 5
        assert source.is_active is True
        assert health.status == "healthy"
        assert health.consecutive_failures == 0

    async def test_item_crud(self, db_session):
        tenant = Tenant(name="Item Tenant", slug="item-tenant")
        db_session.add(tenant)
        await db_session.flush()

        category = Category(
            tenant_id=tenant.id, name="Custom", slug="custom", type="custom"
        )
        db_session.add(category)
        await db_session.flush()

        source = Source(
            tenant_id=tenant.id,
            category_id=category.id,
            name="My Source",
            source_type="api",
            url="https://api.example.com",
        )
        db_session.add(source)
        await db_session.flush()

        item = Item(
            tenant_id=tenant.id,
            category_id=category.id,
            source_id=source.id,
            title="Test Item",
            url="https://example.com/item1",
            published_at=datetime.now(UTC),
            fetched_at=datetime.now(UTC),
        )
        db_session.add(item)
        await db_session.commit()

        assert item.id is not None
        assert item.priority == 5
        assert item.is_processed is True
        assert item.topic_tags == []
        assert item.extra_data == {}

    async def test_finance_symbol_crud(self, db_session):
        tenant = Tenant(name="Fin Tenant", slug="fin-tenant")
        db_session.add(tenant)
        await db_session.flush()

        symbol = FinanceSymbol(
            tenant_id=tenant.id,
            symbol="AAPL",
            name="Apple Inc.",
            type="stock",
            market="US",
        )
        db_session.add(symbol)
        await db_session.commit()

        assert symbol.id is not None
        assert symbol.is_active is True

    async def test_sse_connection_crud(self, db_session):
        tenant = Tenant(name="SSE Tenant", slug="sse-tenant")
        db_session.add(tenant)
        await db_session.flush()

        user = User(
            tenant_id=tenant.id,
            email="sse@example.com",
            name="SSE User",
            sso_provider="google",
            sso_provider_id="sse-google-1",
        )
        db_session.add(user)
        await db_session.flush()

        conn = SSEConnection(
            tenant_id=tenant.id,
            user_id=user.id,
            channels=["news", "tech"],
            connected_at=datetime.now(UTC),
        )
        db_session.add(conn)
        await db_session.commit()

        assert conn.id is not None
        assert conn.channels == ["news", "tech"]

    async def test_dashboard_snapshot_crud(self, db_session):
        tenant = Tenant(name="Dash Tenant", slug="dash-tenant")
        db_session.add(tenant)
        await db_session.flush()

        snapshot = DashboardSnapshot(
            tenant_id=tenant.id,
            cpu_usage_percent=45.5,
            memory_used_mb=1024,
            memory_total_mb=2048,
            timestamp=datetime.now(UTC),
        )
        db_session.add(snapshot)
        await db_session.commit()

        assert snapshot.id is not None
        assert snapshot.cpu_usage_percent == 45.5
