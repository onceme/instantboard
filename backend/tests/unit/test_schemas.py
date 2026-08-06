from datetime import datetime

import pytest
from pydantic import ValidationError

from app.schemas.admin import (
    TenantCreate,
    TenantResponse,
    TenantStatsResponse,
    TenantUpdate,
)
from app.schemas.auth import (
    LogoutResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    SSOLoginRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.category import (
    CategoryCreate,
    CategoryListParams,
    CategoryResponse,
    CategoryUpdate,
    CategoryWithSourcesResponse,
    SubCategoryResponse,
)
from app.schemas.dashboard import (
    CpuMemoryInfo,
    DatabaseStatus,
    DataSourceHealthDetail,
    DataSourceHealthDetailResponse,
    DataSourceHealthResponse,
    DataSourceHealthSummary,
    DiskInfo,
    NetworkInfo,
    SchedulerJobInfo,
    SchedulerStatusResponse,
    ServiceHealthResponse,
    SSEStatsResponse,
    SystemInfoResponse,
    SystemMetricUpdate,
)
from app.schemas.finance import (
    CommodityResponse,
    FinanceQuoteResponse,
    FinanceSearchResult,
    FundNAVResponse,
    MarketIndexResponse,
    QuoteDetailResponse,
    UnderlyingIndexInfo,
    WatchlistItemCreate,
    WatchlistItemResponse,
    WatchlistOrderUpdate,
    WatchlistReorderRequest,
)
from app.schemas.item import ItemResponse
from app.schemas.source import (
    HealthCheckResult,
    SourceCreate,
    SourceHealthResponse,
    SourceListParams,
    SourceResponse,
    SourceUpdate,
)
from app.schemas.sse import SSEConnectionStatus
from app.schemas.tech import TechNewsParams, TechNewsResponse, TechTopicResponse


class TestSSOLoginRequest:
    def test_valid(self):
        req = SSOLoginRequest(code="abc123", redirect_uri="https://example.com/callback")
        assert req.code == "abc123"
        assert req.redirect_uri == "https://example.com/callback"

    def test_missing_code_raises(self):
        with pytest.raises(ValidationError):
            SSOLoginRequest(redirect_uri="https://example.com/callback")

    def test_missing_redirect_uri_raises(self):
        with pytest.raises(ValidationError):
            SSOLoginRequest(code="abc123")


class TestTokenResponse:
    def test_with_all_fields(self):
        user = UserResponse(
            id="u1", email="e@e.com", name="N", tenant_id="t1", role="admin", sso_provider="google"
        )
        resp = TokenResponse(
            access_token="at",
            refresh_token="rt",
            token_type="Bearer",
            expires_in=3600,
            user=user,
        )
        assert resp.access_token == "at"
        assert resp.expires_in == 3600
        assert resp.user is not None
        assert resp.user.email == "e@e.com"

    def test_default_token_type(self):
        resp = TokenResponse(access_token="at", refresh_token="rt", expires_in=3600)
        assert resp.token_type == "Bearer"
        assert resp.user is None

    def test_missing_required_fields_raises(self):
        with pytest.raises(ValidationError):
            TokenResponse(access_token="at")


class TestRefreshTokenRequest:
    def test_valid(self):
        req = RefreshTokenRequest(refresh_token="abc")
        assert req.refresh_token == "abc"


class TestRefreshTokenResponse:
    def test_valid(self):
        resp = RefreshTokenResponse(access_token="a", refresh_token="r", expires_in=100)
        assert resp.access_token == "a"
        assert resp.expires_in == 100


class TestUserResponse:
    def test_full(self):
        u = UserResponse(
            id="id1",
            email="a@b.com",
            name="Name",
            avatar_url="https://pic.com/a.png",
            tenant_id="t1",
            role="admin",
            sso_provider="google",
        )
        assert u.avatar_url == "https://pic.com/a.png"
        assert u.role == "admin"

    def test_optional_avatar_none(self):
        u = UserResponse(id="i", email="e@e.com", name="n", tenant_id="t", role="r", sso_provider="google")
        assert u.avatar_url is None


class TestLogoutResponse:
    def test_default(self):
        resp = LogoutResponse()
        assert resp.message == "Logged out"

    def test_custom(self):
        resp = LogoutResponse(message="Bye")
        assert resp.message == "Bye"


class TestCategoryCreate:
    def test_minimal(self):
        c = CategoryCreate(name="Tech", type="tech")
        assert c.name == "Tech"
        assert c.type == "tech"
        assert c.slug is None
        assert c.icon == "folder"
        assert c.color == "#3B82F6"
        assert c.refresh_interval_seconds == 300
        assert c.keywords_filter is None
        assert c.is_active is True

    def test_full(self):
        c = CategoryCreate(
            name="Finance", slug="finance", description="Finance news", icon="dollar",
            color="#FF0000", type="finance", refresh_interval_seconds=60,
            keywords_filter=["stock", "bond"], is_active=False,
        )
        assert c.slug == "finance"
        assert c.keywords_filter == ["stock", "bond"]
        assert c.is_active is False

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="x" * 51, type="tech")

    def test_slug_too_long_raises(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="n", slug="x" * 51, type="tech")

    def test_description_too_long_raises(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="n", description="x" * 201, type="tech")

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="n", type="invalid")

    def test_refresh_interval_below_min_raises(self):
        with pytest.raises(ValidationError):
            CategoryCreate(name="n", type="tech", refresh_interval_seconds=9)

    def test_valid_types(self):
        for t in ["finance", "tech", "news", "custom"]:
            c = CategoryCreate(name="n", type=t)
            assert c.type == t


class TestCategoryUpdate:
    def test_all_none_defaults(self):
        u = CategoryUpdate()
        assert u.name is None
        assert u.slug is None
        assert u.type is None
        assert u.priority_sort is None
        assert u.is_active is None

    def test_partial_update(self):
        u = CategoryUpdate(name="New Name", refresh_interval_seconds=120)
        assert u.name == "New Name"
        assert u.refresh_interval_seconds == 120

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            CategoryUpdate(name="x" * 51)

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            CategoryUpdate(type="bad")


class TestCategoryResponse:
    def test_full(self):
        now = datetime.now()
        r = CategoryResponse(
            id="c1", name="Tech", slug="tech", description=None, icon="folder",
            color="#3B82F6", type="tech", refresh_interval_seconds=300,
            is_active=True, source_count=5, created_at=now, updated_at=now,
        )
        assert r.id == "c1"
        assert r.source_count == 5
        assert r.created_at == now

    def test_default_source_count(self):
        now = datetime.now()
        r = CategoryResponse(
            id="c1", name="Tech", slug="tech", description=None, icon="folder",
            color="#3B82F6", type="tech", refresh_interval_seconds=300,
            is_active=True, created_at=now, updated_at=now,
        )
        assert r.source_count == 0


class TestCategoryWithSourcesResponse:
    def test_default_sources(self):
        now = datetime.now()
        r = CategoryWithSourcesResponse(
            id="c1", name="T", slug="t", description=None, icon="folder",
            color="#3B82F6", type="tech", refresh_interval_seconds=300,
            is_active=True, created_at=now, updated_at=now,
        )
        assert r.sources == []
        assert r.source_count == 0


class TestSubCategoryResponse:
    def test_with_label(self):
        r = SubCategoryResponse(tag="ai", label="AI", count=5)
        assert r.tag == "ai"
        assert r.label == "AI"

    def test_label_none(self):
        r = SubCategoryResponse(tag="ai", count=3)
        assert r.label is None


class TestCategoryListParams:
    def test_defaults(self):
        p = CategoryListParams()
        assert p.type is None
        assert p.page == 1
        assert p.page_size == 20

    def test_valid_type(self):
        p = CategoryListParams(type="finance")
        assert p.type == "finance"

    def test_invalid_type_raises(self):
        with pytest.raises(ValidationError):
            CategoryListParams(type="bad")

    def test_page_below_min_raises(self):
        with pytest.raises(ValidationError):
            CategoryListParams(page=0)

    def test_page_size_above_max_raises(self):
        with pytest.raises(ValidationError):
            CategoryListParams(page_size=101)


class TestSourceCreate:
    def test_minimal(self):
        s = SourceCreate(name="RSS Source", category_id="c1", source_type="rss", url="https://rss.com/feed")
        assert s.name == "RSS Source"
        assert s.source_type == "rss"
        assert s.config is None
        assert s.refresh_interval_seconds is None
        assert s.is_active is True

    def test_all_fields(self):
        s = SourceCreate(
            name="API", category_id="c2", source_type="api", url="https://api.com",
            config={"key": "val"}, refresh_interval_seconds=60, is_active=False,
        )
        assert s.config == {"key": "val"}
        assert s.is_active is False

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            SourceCreate(name="x" * 101, category_id="c", source_type="rss", url="http://a")

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValidationError):
            SourceCreate(name="S", category_id="c", source_type="invalid", url="http://a")

    def test_refresh_interval_below_min_raises(self):
        with pytest.raises(ValidationError):
            SourceCreate(name="S", category_id="c", source_type="rss", url="http://a", refresh_interval_seconds=5)

    def test_valid_source_types(self):
        for st in ["rss", "api", "web_scrape", "social"]:
            s = SourceCreate(name="S", category_id="c", source_type=st, url="http://a")
            assert s.source_type == st


class TestSourceUpdate:
    def test_all_none(self):
        u = SourceUpdate()
        assert u.name is None
        assert u.source_type is None
        assert u.url is None
        assert u.priority is None

    def test_priority_range(self):
        u = SourceUpdate(priority=10)
        assert u.priority == 10

    def test_priority_above_max_raises(self):
        with pytest.raises(ValidationError):
            SourceUpdate(priority=11)

    def test_priority_below_min_raises(self):
        with pytest.raises(ValidationError):
            SourceUpdate(priority=0)

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValidationError):
            SourceUpdate(source_type="bad")


class TestSourceResponse:
    def test_full(self):
        now = datetime.now()
        r = SourceResponse(
            id="s1", name="Src", category_id="c1", source_type="rss",
            url="http://a", config={}, refresh_interval_seconds=300,
            is_active=True, priority=5, health_status="healthy",
            last_fetch_at=now, last_error=None, created_at=now, updated_at=now,
        )
        assert r.id == "s1"
        assert r.health_status == "healthy"

    def test_optional_health_fields(self):
        now = datetime.now()
        r = SourceResponse(
            id="s1", name="Src", category_id="c1", source_type="rss",
            url="http://a", config={}, refresh_interval_seconds=300,
            is_active=True, priority=5, created_at=now, updated_at=now,
        )
        assert r.health_status is None
        assert r.last_fetch_at is None
        assert r.last_error is None


class TestSourceHealthResponse:
    def test_full(self):
        r = SourceHealthResponse(
            source_id="src1", status="healthy", success_rate_24h=99.5,
            avg_response_time_ms=150, last_success_at=datetime.now(),
            last_failure_at=None, consecutive_failures=0,
            total_fetches_24h=100, last_error=None,
        )
        assert r.status == "healthy"
        assert r.success_rate_24h == 99.5

    def test_optional_fields(self):
        r = SourceHealthResponse(
            source_id="src1", status="down", consecutive_failures=5, total_fetches_24h=0,
        )
        assert r.success_rate_24h is None
        assert r.last_error is None


class TestHealthCheckResult:
    def test_success(self):
        r = HealthCheckResult(success=True)
        assert r.success is True
        assert r.response_time_ms == 0
        assert r.error_message is None

    def test_failure(self):
        r = HealthCheckResult(success=False, response_time_ms=500, error_message="timeout")
        assert r.success is False
        assert r.error_message == "timeout"


class TestSourceListParams:
    def test_defaults(self):
        p = SourceListParams()
        assert p.category_id is None
        assert p.source_type is None
        assert p.status is None
        assert p.is_active is None
        assert p.page == 1
        assert p.page_size == 20

    def test_valid_status(self):
        for s in ["healthy", "degraded", "down"]:
            p = SourceListParams(status=s)
            assert p.status == s

    def test_invalid_status_raises(self):
        with pytest.raises(ValidationError):
            SourceListParams(status="unknown")

    def test_invalid_source_type_raises(self):
        with pytest.raises(ValidationError):
            SourceListParams(source_type="bad")


class TestItemResponse:
    def test_full(self):
        r = ItemResponse(
            id="i1", title="Test", summary="A summary", url="http://example.com",
            image_url="http://img.com/a.png", source_name="Src", source_id="s1",
            category_id="c1", topic_tags=["ai", "news"],
            published_at=datetime.now(), fetched_at=datetime.now(), priority=5,
        )
        assert r.title == "Test"
        assert r.topic_tags == ["ai", "news"]
        assert r.priority == 5

    def test_optional_fields(self):
        r = ItemResponse(
            id="i1", title="T", summary=None, url="http://x.com",
            image_url=None, source_name=None, source_id="s1",
            category_id="c1", topic_tags=[],
            published_at=datetime.now(), fetched_at=datetime.now(), priority=1,
        )
        assert r.summary is None
        assert r.image_url is None
        assert r.source_name is None


class TestFinanceSearchResult:
    def test_full(self):
        r = FinanceSearchResult(
            symbol="AAPL", name="Apple Inc.", type="stock", market="US",
            exchange="NASDAQ", current_price=150.5, change_percent=1.2, currency="USD",
        )
        assert r.symbol == "AAPL"
        assert r.current_price == 150.5

    def test_optional_fields(self):
        r = FinanceSearchResult(
            symbol="ETH", name="Ethereum", type="currency", market="Crypto",
            exchange=None, current_price=None, change_percent=None, currency="USD",
        )
        assert r.exchange is None
        assert r.current_price is None


class TestFinanceQuoteResponse:
    def test_full(self):
        r = FinanceQuoteResponse(
            symbol="AAPL", name="Apple", current_price=150.0, open=148.0,
            high=152.0, low=147.0, close_previous=148.5, volume=1000000,
            change=1.5, change_percent=1.0, market_cap=2000000000, pe_ratio=25.5,
            **{"52_week_high": 180.0, "52_week_low": 120.0},
            timestamp=datetime.now(), source="yfinance",
        )
        assert r.symbol == "AAPL"
        assert r.week_high_52 == 180.0
        assert r.week_low_52 == 120.0

    def test_populate_by_name(self):
        r = FinanceQuoteResponse(
            symbol="AAPL", name="Apple",
            current_price=None, open=None, high=None, low=None,
            close_previous=None, volume=None, change=None, change_percent=None,
            market_cap=None, pe_ratio=None,
            week_high_52=None, week_low_52=None,
            timestamp=None, source=None,
        )
        assert r.week_high_52 is None

    def test_all_nullable(self):
        r = FinanceQuoteResponse(symbol="X", name="X")
        assert r.current_price is None
        assert r.open is None
        assert r.high is None
        assert r.low is None
        assert r.close_previous is None
        assert r.volume is None
        assert r.change is None
        assert r.change_percent is None
        assert r.market_cap is None
        assert r.pe_ratio is None
        assert r.week_high_52 is None
        assert r.week_low_52 is None
        assert r.timestamp is None
        assert r.source is None


class TestMarketIndexResponse:
    def test_full(self):
        r = MarketIndexResponse(
            symbol="SPX", name="S&P 500", value=4500.5, change=20.3,
            change_percent=0.45, market_status="open", region="US", timestamp=datetime.now(),
        )
        assert r.symbol == "SPX"
        assert r.market_status == "open"


class TestCommodityResponse:
    def test_full(self):
        r = CommodityResponse(
            symbol="GC=F", name="Gold", value=1800.5, change=-5.2,
            change_percent=-0.29, unit="USD/oz", timestamp=datetime.now(),
        )
        assert r.unit == "USD/oz"

    def test_all_nullable(self):
        r = CommodityResponse(symbol="X", name="X")
        assert r.value is None
        assert r.change is None
        assert r.timestamp is None


class TestUnderlyingIndexInfo:
    def test_full(self):
        r = UnderlyingIndexInfo(symbol="SPX", name="S&P 500", current_value=4500.0, change_percent=0.5)
        assert r.symbol == "SPX"

    def test_nullable(self):
        r = UnderlyingIndexInfo(symbol="X", name="X")
        assert r.current_value is None
        assert r.change_percent is None


class TestFundNAVResponse:
    def test_full(self):
        idx = UnderlyingIndexInfo(symbol="SPX", name="S&P 500", current_value=4500.0, change_percent=0.5)
        r = FundNAVResponse(
            symbol="VOO", name="Vanguard S&P 500", nav_official=400.5,
            nav_official_date="2024-01-01", nav_estimate=401.0,
            nav_estimate_deviation_percent=0.12, estimate_method="index",
            estimate_timestamp=datetime.now(), underlying_index=idx,
        )
        assert r.underlying_index.symbol == "SPX"

    def test_nullable(self):
        r = FundNAVResponse(symbol="X", name="X")
        assert r.nav_official is None
        assert r.underlying_index is None


class TestWatchlistItemCreate:
    def test_minimal(self):
        c = WatchlistItemCreate(symbol_id="sym1")
        assert c.symbol_id == "sym1"
        assert c.display_order == 0
        assert c.notes is None
        assert c.alert_threshold_percent is None

    def test_full(self):
        c = WatchlistItemCreate(
            symbol_id="s1", display_order=3, notes="My note", alert_threshold_percent=5.0,
        )
        assert c.notes == "My note"

    def test_notes_too_long_raises(self):
        with pytest.raises(ValidationError):
            WatchlistItemCreate(symbol_id="s1", notes="x" * 201)


class TestWatchlistOrderUpdate:
    def test_valid(self):
        u = WatchlistOrderUpdate(item_id="wi1", display_order=2)
        assert u.item_id == "wi1"
        assert u.display_order == 2


class TestWatchlistReorderRequest:
    def test_valid(self):
        items = [WatchlistOrderUpdate(item_id="a", display_order=0), WatchlistOrderUpdate(item_id="b", display_order=1)]
        req = WatchlistReorderRequest(items=items)
        assert len(req.items) == 2

    def test_empty_list(self):
        req = WatchlistReorderRequest(items=[])
        assert req.items == []


class TestWatchlistItemResponse:
    def test_full(self):
        r = WatchlistItemResponse(
            id="wi1", symbol_id="s1", symbol="AAPL", name="Apple",
            display_order=0, notes="Hold", alert_threshold_percent=5.0,
            current_price=150.0, change=2.0, change_percent=1.35,
        )
        assert r.symbol == "AAPL"

    def test_nullable(self):
        r = WatchlistItemResponse(id="wi1", symbol_id="s1", display_order=0)
        assert r.symbol is None
        assert r.name is None
        assert r.notes is None
        assert r.current_price is None


class TestQuoteDetailResponse:
    def test_full(self):
        r = QuoteDetailResponse(
            symbol="AAPL", name="Apple", current_price=150.0, open=148.0,
            high=152.0, low=147.0, close_previous=148.5, volume=1000000,
            change=1.5, change_percent=1.0, market_cap=2000000000, pe_ratio=25.5,
            week_high_52=180.0, week_low_52=120.0, timestamp=datetime.now(), source="yfinance",
        )
        assert r.market_cap == 2000000000

    def test_all_nullable(self):
        r = QuoteDetailResponse(symbol="X", name="X")
        assert r.volume is None
        assert r.week_high_52 is None
        assert r.source is None


class TestTechNewsResponse:
    def test_full(self):
        r = TechNewsResponse(
            id="i1", title="AI News", summary="Details", url="http://a.com",
            source_name="TechCrunch", source_id="s1", category_id="c1",
            topic_tags=["ai", "ml"], domain_tag="AI", published_at=datetime.now(),
            fetched_at=datetime.now(), image_url=None, priority=5,
            extra_data={"key": "val"}, hot_score=95.5,
        )
        assert r.domain_tag == "AI"
        assert r.hot_score == 95.5
        assert r.extra_data == {"key": "val"}

    def test_nullable(self):
        r = TechNewsResponse(
            id="i1", title="T", summary=None, url="http://x.com",
            source_name=None, source_id="s1", category_id="c1",
            topic_tags=[], domain_tag=None, published_at=datetime.now(),
            fetched_at=datetime.now(), image_url=None, priority=1,
            extra_data=None, hot_score=None,
        )
        assert r.summary is None
        assert r.domain_tag is None


class TestTechTopicResponse:
    def test_full(self):
        r = TechTopicResponse(tag="ai", label="Artificial Intelligence", count=10, last_active_at=datetime.now())
        assert r.label == "Artificial Intelligence"

    def test_nullable_label(self):
        r = TechTopicResponse(tag="ml", count=5)
        assert r.label is None
        assert r.last_active_at is None


class TestTechNewsParams:
    def test_defaults(self):
        p = TechNewsParams()
        assert p.domain is None
        assert p.subcategory is None
        assert p.sort == "hot"
        assert p.page == 1
        assert p.page_size == 20

    def test_valid_sort(self):
        for s in ["hot", "time", "relevance"]:
            p = TechNewsParams(sort=s)
            assert p.sort == s

    def test_invalid_sort_raises(self):
        with pytest.raises(ValidationError):
            TechNewsParams(sort="bad")

    def test_page_zero_raises(self):
        with pytest.raises(ValidationError):
            TechNewsParams(page=0)

    def test_page_size_too_large_raises(self):
        with pytest.raises(ValidationError):
            TechNewsParams(page_size=101)


class TestSSEConnectionStatus:
    def test_full(self):
        r = SSEConnectionStatus(
            active_channels=["news", "tech"],
            connection_id="conn1",
            connected_since=datetime.now(),
        )
        assert r.active_channels == ["news", "tech"]
        assert r.connection_id == "conn1"

    def test_connected_since_none(self):
        r = SSEConnectionStatus(active_channels=[], connection_id="c1")
        assert r.connected_since is None


class TestCpuMemoryInfo:
    def test_all_none(self):
        r = CpuMemoryInfo()
        assert r.cpu_usage_percent is None
        assert r.cpu_count is None
        assert r.memory_total_mb is None
        assert r.memory_used_mb is None
        assert r.memory_usage_percent is None

    def test_with_values(self):
        r = CpuMemoryInfo(cpu_usage_percent=55.0, cpu_count=8, memory_total_mb=16384)
        assert r.cpu_count == 8


class TestDiskInfo:
    def test_all_none(self):
        r = DiskInfo()
        assert r.disk_total_gb is None
        assert r.disk_used_gb is None
        assert r.disk_usage_percent is None

    def test_with_values(self):
        r = DiskInfo(disk_total_gb=500.0, disk_used_gb=250.0, disk_usage_percent=50.0)
        assert r.disk_usage_percent == 50.0


class TestNetworkInfo:
    def test_all_none(self):
        r = NetworkInfo()
        assert r.bytes_sent is None
        assert r.bytes_recv is None
        assert r.packets_sent is None
        assert r.packets_recv is None


class TestDatabaseStatus:
    def test_all_none(self):
        r = DatabaseStatus()
        assert r.postgres_connections is None
        assert r.postgres_active_queries is None
        assert r.redis_connected is None
        assert r.redis_memory_used_mb is None

    def test_with_values(self):
        r = DatabaseStatus(postgres_connections=10, redis_connected=True)
        assert r.postgres_connections == 10
        assert r.redis_connected is True


class TestSystemInfoResponse:
    def test_minimal(self):
        r = SystemInfoResponse(
            uptime_seconds=3600, environment="production", python_version="3.11.5",
        )
        assert r.version == "1.0.0"
        assert r.uptime_seconds == 3600
        assert r.cpu is None
        assert r.memory is None
        assert r.disk is None
        assert r.network is None
        assert r.database is None
        assert r.cpu_count is None
        assert r.cpu_usage_percent is None
        assert r.memory_total_mb is None
        assert r.memory_used_mb is None
        assert r.disk_total_gb is None
        assert r.disk_used_gb is None

    def test_with_nested_objects(self):
        cpu = CpuMemoryInfo(cpu_usage_percent=30.0, cpu_count=4)
        disk = DiskInfo(disk_total_gb=500.0)
        r = SystemInfoResponse(
            uptime_seconds=7200, environment="dev", python_version="3.12",
            cpu=cpu, disk=disk,
        )
        assert r.cpu.cpu_usage_percent == 30.0
        assert r.disk.disk_total_gb == 500.0

    def test_default_version(self):
        r = SystemInfoResponse(uptime_seconds=0, environment="test", python_version="3.11")
        assert r.version == "1.0.0"


class TestServiceHealthResponse:
    def test_full(self):
        r = ServiceHealthResponse(
            service="redis", status="healthy", response_time_ms=5,
            connection_count=3, details={"version": "7.0"},
        )
        assert r.service == "redis"
        assert r.details == {"version": "7.0"}

    def test_optional(self):
        r = ServiceHealthResponse(service="db", status="healthy")
        assert r.response_time_ms is None
        assert r.connection_count is None
        assert r.details is None


class TestDataSourceHealthDetail:
    def test_full(self):
        r = DataSourceHealthDetail(
            id="s1", name="Src", source_type="rss", category_id="c1",
            status="healthy", success_rate_24h=99.0, avg_response_time_ms=100,
            last_success_at=datetime.now(), last_failure_at=None,
            consecutive_failures=0, total_fetches_24h=50, last_error=None,
        )
        assert r.success_rate_24h == 99.0

    def test_nullable(self):
        r = DataSourceHealthDetail(id="s1", name="Src", status="down")
        assert r.source_type is None
        assert r.category_id is None
        assert r.success_rate_24h is None


class TestDataSourceHealthSummary:
    def test_full(self):
        r = DataSourceHealthSummary(total_sources=10, healthy=8, degraded=1, down=1)
        assert r.total_sources == 10
        assert r.healthy == 8


class TestDataSourceHealthResponse:
    def test_full(self):
        detail = DataSourceHealthDetail(id="s1", name="S", status="healthy")
        r = DataSourceHealthResponse(
            total_sources=1, healthy=1, degraded=0, down=0, sources=[detail],
        )
        assert len(r.sources) == 1
        assert r.sources[0].name == "S"

    def test_empty_sources(self):
        r = DataSourceHealthResponse(total_sources=0, healthy=0, degraded=0, down=0, sources=[])
        assert r.sources == []


class TestDataSourceHealthDetailResponse:
    def test_full(self):
        r = DataSourceHealthDetailResponse(
            source_id="s1", name="Src", source_type="rss", status="healthy",
            success_rate_24h=95.0, avg_response_time_ms=200,
            last_success_at=datetime.now(), last_failure_at=None,
            consecutive_failures=0, total_fetches_24h=100,
            last_error=None, health_history=[{"ts": "2024"}],
            response_time_trend=[{"ts": "2024", "ms": 100}],
        )
        assert r.health_history == [{"ts": "2024"}]

    def test_nullable(self):
        r = DataSourceHealthDetailResponse(source_id="s1", name="S", status="down")
        assert r.source_type is None
        assert r.health_history is None
        assert r.response_time_trend is None


class TestSchedulerJobInfo:
    def test_full(self):
        r = SchedulerJobInfo(
            job_id="j1", source_id="s1", name="Fetch RSS",
            schedule="*/5 * * * *", original_interval=300, current_interval=300,
            adaptive_multiplier=1.0, last_run="2024-01-01", next_run="2024-01-02",
            status="running", success_count_24h=100, failure_count_24h=0,
        )
        assert r.job_id == "j1"
        assert r.adaptive_multiplier == 1.0

    def test_nullable(self):
        r = SchedulerJobInfo(job_id="j1", name="Test", status="paused")
        assert r.source_id is None
        assert r.schedule is None
        assert r.last_run is None
        assert r.success_count_24h is None


class TestSchedulerStatusResponse:
    def test_full(self):
        job = SchedulerJobInfo(job_id="j1", name="J", status="running")
        r = SchedulerStatusResponse(
            total_jobs=1, running_jobs=[job], paused_jobs=[], all_jobs=[job],
        )
        assert r.total_jobs == 1
        assert len(r.running_jobs) == 1
        assert r.paused_jobs == []

    def test_empty(self):
        r = SchedulerStatusResponse(total_jobs=0, running_jobs=[], paused_jobs=[], all_jobs=[])
        assert r.total_jobs == 0


class TestSSEStatsResponse:
    def test_full(self):
        r = SSEStatsResponse(
            total_connections=50, connections_by_channel={"news": 30, "tech": 20},
            peak_connections_24h=100, peak_connections_today=80,
            total_connections_today=500, total_events_pushed=10000,
            average_events_per_minute=15.5, avg_connection_duration_seconds=300.0,
        )
        assert r.connections_by_channel == {"news": 30, "tech": 20}
        assert r.peak_connections_24h == 100

    def test_optional(self):
        r = SSEStatsResponse(
            total_connections=1, connections_by_channel={}, peak_connections_24h=1,
        )
        assert r.peak_connections_today is None
        assert r.total_events_pushed is None


class TestSystemMetricUpdate:
    def test_all_none(self):
        r = SystemMetricUpdate()
        assert r.cpu_usage_percent is None
        assert r.memory_usage_percent is None
        assert r.disk_usage_percent is None
        assert r.network_bytes_sent is None
        assert r.network_bytes_recv is None
        assert r.timestamp is None

    def test_with_values(self):
        r = SystemMetricUpdate(
            cpu_usage_percent=55.5,
            memory_usage_percent=60.0,
            timestamp="2024-01-01T00:00:00Z",
        )
        assert r.cpu_usage_percent == 55.5
        assert r.timestamp == "2024-01-01T00:00:00Z"


class TestTenantCreate:
    def test_minimal(self):
        t = TenantCreate(name="Acme", slug="acme")
        assert t.name == "Acme"
        assert t.slug == "acme"
        assert t.plan == "free"
        assert t.settings is None
        assert t.max_users == 5
        assert t.max_categories == 10
        assert t.max_sources == 50

    def test_full(self):
        t = TenantCreate(
            name="Pro Co", slug="pro-co", plan="pro",
            settings={"custom": True}, max_users=100, max_categories=50, max_sources=500,
        )
        assert t.plan == "pro"
        assert t.max_users == 100

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            TenantCreate(name="x" * 101, slug="s")

    def test_slug_too_long_raises(self):
        with pytest.raises(ValidationError):
            TenantCreate(name="n", slug="x" * 51)

    def test_invalid_plan_raises(self):
        with pytest.raises(ValidationError):
            TenantCreate(name="n", slug="s", plan="invalid")

    def test_valid_plans(self):
        for p in ["free", "pro", "enterprise"]:
            t = TenantCreate(name="n", slug="s", plan=p)
            assert t.plan == p


class TestTenantUpdate:
    def test_all_none(self):
        u = TenantUpdate()
        assert u.name is None
        assert u.slug is None
        assert u.plan is None
        assert u.settings is None
        assert u.max_users is None
        assert u.is_active is None

    def test_partial(self):
        u = TenantUpdate(is_active=False, plan="enterprise")
        assert u.is_active is False
        assert u.plan == "enterprise"

    def test_invalid_plan_raises(self):
        with pytest.raises(ValidationError):
            TenantUpdate(plan="gold")

    def test_name_too_long_raises(self):
        with pytest.raises(ValidationError):
            TenantUpdate(name="x" * 101)


class TestTenantResponse:
    def test_full(self):
        now = datetime.now()
        r = TenantResponse(
            id="t1", name="Acme", slug="acme", plan="free",
            settings={}, max_users=5, max_categories=10, max_sources=50,
            is_active=True, created_at=now, updated_at=now,
        )
        assert r.id == "t1"
        assert r.plan == "free"
        assert r.is_active is True


class TestTenantStatsResponse:
    def test_full(self):
        r = TenantStatsResponse(
            tenant_id="t1", user_count=5, category_count=3,
            source_count=10, item_count=100, active_sse_connections=2,
        )
        assert r.tenant_id == "t1"
        assert r.item_count == 100


class TestSchemasInit:
    def test_empty_init(self):
        import app.schemas
        assert app.schemas.__file__.endswith("__init__.py")
