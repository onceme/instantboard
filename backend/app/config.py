import json
import logging
from typing import Annotated, Any

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

logger = logging.getLogger(__name__)

# Environments in which an unsafe JWT secret must abort startup (fail-fast).
JWT_SECRET_REQUIRED_ENVS = frozenset({"staging", "production"})
# Minimum acceptable length for JWT_SECRET (256 bits of entropy for HS256).
JWT_SECRET_MIN_LENGTH = 32
# Known shipped/example values that must never be used as a real signing secret.
# Note the second one is longer than the minimum length, so an explicit
# placeholder check is required in addition to the length check.
JWT_SECRET_PLACEHOLDERS = frozenset(
    {
        "change-this-in-production",
        "change-this-in-production-use-a-strong-random-key",
    }
)


def _parse_list_str(v: object) -> list[str]:
    if isinstance(v, list):
        return v
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return []
    if isinstance(v, str):
        stripped = v.strip()
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except (json.JSONDecodeError, ValueError):
            pass
        return [item.strip() for item in stripped.split(",") if item.strip()]
    return v


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="development", alias="ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+asyncpg://instantboard:devpass@localhost:5432/instantboard_dev",
        alias="DATABASE_URL",
    )
    database_pool_size: int = Field(default=20, alias="DATABASE_POOL_SIZE")
    database_max_overflow: int = Field(default=10, alias="DATABASE_MAX_OVERFLOW")
    database_pool_recycle: int = Field(default=3600, alias="DATABASE_POOL_RECYCLE")

    # Redis
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    redis_max_memory: str = Field(default="512mb", alias="REDIS_MAX_MEMORY")

    # MongoDB (not enabled in the initial version)
    mongodb_url: str | None = Field(default=None, alias="MONGODB_URL")

    # JWT
    jwt_secret: str = Field(default="change-this-in-production", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(default=60, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    # Local Admin Login (isolated admin identity, see docs/dev-guide/design/admin-login.md)
    admin_email: str | None = Field(default=None, alias="ADMIN_EMAIL")
    admin_password_hash: str | None = Field(default=None, alias="ADMIN_PASSWORD_HASH")
    admin_password: str | None = Field(default=None, alias="ADMIN_PASSWORD")

    # SSO Enabled Providers
    enabled_sso_providers: Annotated[list[str], NoDecode] = Field(
        default=["google", "github"],
        alias="ENABLED_SSO_PROVIDERS",
    )

    # SSO - Google
    google_oauth_client_id: str | None = Field(default=None, alias="GOOGLE_OAUTH_CLIENT_ID")
    google_oauth_client_secret: str | None = Field(default=None, alias="GOOGLE_OAUTH_CLIENT_SECRET")

    # SSO - Azure AD
    azure_ad_client_id: str | None = Field(default=None, alias="AZURE_AD_CLIENT_ID")
    azure_ad_client_secret: str | None = Field(default=None, alias="AZURE_AD_CLIENT_SECRET")
    azure_ad_tenant_id: str = Field(default="common", alias="AZURE_AD_TENANT_ID")

    # SSO - GitHub
    github_oauth_client_id: str | None = Field(default=None, alias="GITHUB_OAUTH_CLIENT_ID")
    github_oauth_client_secret: str | None = Field(default=None, alias="GITHUB_OAUTH_CLIENT_SECRET")

    # SSO - Apple
    apple_client_id: str | None = Field(default=None, alias="APPLE_CLIENT_ID")
    apple_team_id: str | None = Field(default=None, alias="APPLE_TEAM_ID")
    apple_key_id: str | None = Field(default=None, alias="APPLE_KEY_ID")
    apple_private_key_path: str | None = Field(default=None, alias="APPLE_PRIVATE_KEY_PATH")

    # SSO - Facebook
    facebook_app_id: str | None = Field(default=None, alias="FACEBOOK_APP_ID")
    facebook_app_secret: str | None = Field(default=None, alias="FACEBOOK_APP_SECRET")

    # Financial Data API Keys
    yahoo_finance_api_key: str | None = Field(default=None, alias="YAHOO_FINANCE_API_KEY")
    alpha_vantage_api_key: str | None = Field(default=None, alias="ALPHA_VANTAGE_API_KEY")
    # Comma-separated list of keys; the collector rotates through them round-robin.
    # Takes precedence over alpha_vantage_api_key when non-empty.
    alpha_vantage_api_keys: str | None = Field(default=None, alias="ALPHA_VANTAGE_API_KEYS")
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    finnhub_api_keys: Annotated[list[str], NoDecode] = Field(default=[], alias="FINNHUB_API_KEYS")
    # IEX Cloud is an optional US-stock source; the collector stays dormant without a key.
    iex_cloud_api_key: str | None = Field(default=None, alias="IEX_CLOUD_API_KEY")
    # Override to point at a sandbox/test deployment (e.g. https://sandbox.iexapis.com/stable).
    iex_cloud_base_url: str = Field(default="https://cloud.iexapis.com/stable", alias="IEX_CLOUD_BASE_URL")

    # Twitter/X API v2 (optional social tech source); the collector stays dormant without
    # a token. Note: the recent-search endpoint requires a paid Twitter API tier.
    twitter_bearer_token: str | None = Field(default=None, alias="TWITTER_BEARER_TOKEN")

    # CORS
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:3000", "http://localhost:8000"],
        alias="CORS_ORIGINS",
    )

    @field_validator("finnhub_api_keys", "cors_origins", "enabled_sso_providers", mode="before")
    @classmethod
    def parse_list_env_var(cls, v: object) -> list[str]:
        return _parse_list_str(v)

    # SSE
    sse_heartbeat_interval: int = Field(default=30, alias="SSE_HEARTBEAT_INTERVAL")

    # Rate Limiting (security.md §3.3 layer 2): Redis sliding-window limiter.
    # Master switch; when off the RateLimitMiddleware passes everything through.
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    # Default per-minute limit for regular /api/v1/* routes.
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    # auth routes already stack the admin-login email+IP brute-force lockout on top,
    # so this tier only needs a moderate value to contain credential-stuffing
    # volume against the SSO/refresh endpoints.
    rate_limit_auth_per_minute: int = Field(default=10, alias="RATE_LIMIT_AUTH_PER_MINUTE")
    # finance/tech search routes: interactive searches only, keep them tight.
    rate_limit_search_per_minute: int = Field(default=20, alias="RATE_LIMIT_SEARCH_PER_MINUTE")
    # Extra requests tolerated on top of the tier limit within the sliding
    # window, so short legitimate spikes (page-load fan-out) don't trip 429.
    rate_limit_burst: int = Field(default=10, alias="RATE_LIMIT_BURST")

    # IP blacklist (security.md §3.3 layer 3): how many seconds the middleware's
    # in-process copy of the banned-IP set may be served before re-reading Redis.
    # Admin add/remove endpoints invalidate the local cache explicitly, so this
    # only bounds how long OTHER api worker processes may stay stale.
    ip_blacklist_cache_ttl: int = Field(default=30, alias="IP_BLACKLIST_CACHE_TTL")

    # Request Validation (security.md §3.3 layer 4)
    require_user_agent: bool = Field(default=True, alias="REQUIRE_USER_AGENT")
    max_request_body_bytes: int = Field(default=10 * 1024, alias="MAX_REQUEST_BODY_BYTES")

    # Tenant Defaults
    default_tenant_slug: str = Field(default="default", alias="DEFAULT_TENANT_SLUG")
    default_tenant_name: str = Field(default="Default Tenant", alias="DEFAULT_TENANT_NAME")

    # Scheduler
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")
    # Periodic market indices / commodities cache refresh (finance-tab.md §3.8.2):
    # interval jobs that re-fill the Redis cache and push SSE, gated on at least
    # one major market being open.
    market_indices_refresh_interval: int = Field(default=30, alias="MARKET_INDICES_REFRESH_INTERVAL")
    commodities_refresh_interval: int = Field(default=60, alias="COMMODITIES_REFRESH_INTERVAL")

    # Fund intraday NAV (fund-intraday-nav.md §10): holdings-weighted intraday
    # estimate of followed funds, computed every N seconds while the CN market
    # is open and pushed via SSE nav_batch_update.
    # Master switch: false skips job registration; REST still degrades to
    # latest_official answers (fund-intraday-nav.md §10).
    fund_nav_intraday_enabled: bool = Field(default=True, alias="FUND_NAV_INTRADAY_ENABLED")
    # Estimate cycle seconds; lower bound 2s is the single-cycle timing budget
    # of the fetch+compute+push pipeline (fund-intraday-nav.md §7.3).
    fund_nav_intraday_refresh_interval: int = Field(default=3, ge=2, alias="FUND_NAV_INTRADAY_REFRESH_INTERVAL")
    # Followed-union cap: codes beyond it are truncated (oldest-followed first
    # kept) so a worst-case union never breaks the cycle budget
    # (fund-intraday-nav.md §7.2/§7.3). 1000 is the hard configuration-error
    # ceiling, not an operating point.
    fund_nav_intraday_max_funds: int = Field(default=500, ge=1, le=1000, alias="FUND_NAV_INTRADAY_MAX_FUNDS")
    # Holdings freshness threshold in days: disclosures older than this fall
    # back from holdings_weighted to index_tracking (/latest_official)
    # (fund-intraday-nav.md §4.3).
    fund_nav_holdings_fresh_days: int = Field(default=120, ge=1, alias="FUND_NAV_HOLDINGS_FRESH_DAYS")
    # Minimum coverage (sum of available holding weights, percent) for the
    # holdings_weighted method; below it the estimator prefers index tracking
    # (fund-intraday-nav.md §4.3).
    fund_nav_holdings_min_coverage: float = Field(default=30.0, ge=0.0, alias="FUND_NAV_HOLDINGS_MIN_COVERAGE")
    # Downsampled DB flush: minimum seconds between two fund_nav_estimates
    # writes per fund (fund-intraday-nav.md §3.4 case 1).
    fund_nav_db_flush_min_gap: int = Field(default=60, ge=1, alias="FUND_NAV_DB_FLUSH_MIN_GAP")
    # Hour (Asia/Shanghai) of the daily holdings refresh cron — after the CN
    # close, when quarterly disclosure updates land (fund-intraday-nav.md
    # §5.1/§5.5).
    fund_holdings_refresh_hour: int = Field(default=18, ge=0, le=23, alias="FUND_HOLDINGS_REFRESH_HOUR")
    # Holdings rows fetched per report period from the EastMoney f10 jjcc
    # endpoint (fund-intraday-nav.md §13 M3 §1.2). The upstream `topline` param
    # caps rows per period; 10 (the historical default) returns only the top-10
    # holdings per period, while semi-annual / annual reports disclose the full
    # book. Default 30 lifts coverage beyond top-10 without being excessive.
    fund_holdings_topline: int = Field(default=30, ge=1, le=100, alias="FUND_HOLDINGS_TOPLINE")

    # Quote upstream budget governance (fund-intraday-nav.md §6): global safety
    # factor applied to every upstream's max_rpm budget, and the IP-safety red
    # line switch that admits the EastMoney push2 MAIN domain into the HK quote
    # chain (default off — the main domain bans clients after ~8 requests,
    # §6.3).
    quote_upstream_safety_factor: float = Field(default=0.8, gt=0.0, le=1.0, alias="QUOTE_UPSTREAM_SAFETY_FACTOR")
    quote_eastmoney_main_enabled: bool = Field(default=False, alias="QUOTE_EASTMONEY_MAIN_ENABLED")
    # Per-upstream budget overrides (fund-intraday-nav.md §6.1 registry
    # defaults; the values below mirror that table so ops can tune without a
    # code change).
    quote_tencent_max_rpm: int = Field(default=120, ge=1, alias="QUOTE_TENCENT_MAX_RPM")
    quote_sina_max_rpm: int = Field(default=60, ge=1, alias="QUOTE_SINA_MAX_RPM")
    quote_em_delay_max_rpm: int = Field(default=40, ge=1, alias="QUOTE_EM_DELAY_MAX_RPM")
    quote_em_f10_max_rpm: int = Field(default=8, ge=1, alias="QUOTE_EM_F10_MAX_RPM")

    # Dashboard snapshot retention (docs/dev-guide/design/dashboard-tab.md §3.9.3):
    # the metrics collection loop purges dashboard_snapshots rows older than this.
    dashboard_snapshot_retention_days: int = Field(default=30, alias="DASHBOARD_SNAPSHOT_RETENTION_DAYS")

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"

    @property
    def admin_login_enabled(self) -> bool:
        return bool(self.admin_email) and bool(self.admin_password_hash)

    def model_post_init(self, __context: Any) -> None:
        if self.admin_email is not None and not self.admin_email.strip():
            self.admin_email = None
        if self.admin_password_hash is not None and not self.admin_password_hash.strip():
            self.admin_password_hash = None
        if self.admin_password is not None and not self.admin_password.strip():
            self.admin_password = None


def apply_admin_password_policy(target: Settings) -> None:
    """Resolve a plaintext ADMIN_PASSWORD into ADMIN_PASSWORD_HASH.

    Allowed for non-production environments only: the password is bcrypt-hashed once at
    startup (with a warning) and the plaintext is dropped from memory. In production a
    configured plaintext is logged as an error and treated as unset. Hashing happens
    outside Settings.model_post_init because app.core.security imports this module, and
    the module-level settings instance is still being constructed at that point.
    """
    if not target.admin_password:
        return
    if target.is_production:
        logger.error(
            "ADMIN_PASSWORD plaintext is forbidden in production and will be ignored; "
            "configure ADMIN_PASSWORD_HASH instead (see scripts/gen_admin_password_hash.py)"
        )
        target.admin_password = None
        return
    from app.core.security import hash_password

    logger.warning(
        "ADMIN_PASSWORD plaintext detected in a non-production environment; hashing it "
        "once at startup. Configure ADMIN_PASSWORD_HASH instead."
    )
    target.admin_password_hash = hash_password(target.admin_password)
    target.admin_password = None


def validate_jwt_secret_policy(target: Settings) -> None:
    """Fail fast when the configured JWT secret is too weak for a live environment.

    Trust model: HS256 tokens are only as strong as the signing secret. In staging and
    production a placeholder or short secret is a startup-blocking error (fail-fast), so
    a misconfigured deployment can never boot with a forgeable signing key. In other
    environments the same condition is logged as a warning without blocking startup.

    The offending value is never included in the message, so the secret itself is not
    leaked into logs or crash output.
    """
    secret = target.jwt_secret
    env = target.env.lower()
    is_placeholder = secret in JWT_SECRET_PLACEHOLDERS
    is_short = len(secret) < JWT_SECRET_MIN_LENGTH
    if not is_placeholder and not is_short:
        return
    requirement = (
        f"JWT_SECRET must be a strong random value of at least {JWT_SECRET_MIN_LENGTH} "
        f"characters and must not be a shipped placeholder. Generate one with e.g. "
        f"'openssl rand -hex 32'."
    )
    if env in JWT_SECRET_REQUIRED_ENVS:
        raise RuntimeError(f"Startup aborted for ENV={env}: unsafe JWT_SECRET. {requirement}")
    logger.warning("Unsafe JWT_SECRET detected for ENV=%s (allowed in non-production). %s", env, requirement)


settings = Settings()
validate_jwt_secret_policy(settings)
apply_admin_password_policy(settings)
