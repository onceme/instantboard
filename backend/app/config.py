import json
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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

    # MongoDB (初始版本不启用)
    mongodb_url: str | None = Field(default=None, alias="MONGODB_URL")

    # JWT
    secret_key: str = Field(default="change-this-in-production", alias="SECRET_KEY")
    jwt_secret: str = Field(default="change-this-in-production", alias="JWT_SECRET")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(default=60, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

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
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    finnhub_api_keys: Annotated[list[str], NoDecode] = Field(default=[], alias="FINNHUB_API_KEYS")

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

    # Rate Limiting
    rate_limit_per_minute: int = Field(default=60, alias="RATE_LIMIT_PER_MINUTE")
    rate_limit_burst: int = Field(default=10, alias="RATE_LIMIT_BURST")

    # Tenant Defaults
    default_tenant_slug: str = Field(default="default", alias="DEFAULT_TENANT_SLUG")
    default_tenant_name: str = Field(default="Default Tenant", alias="DEFAULT_TENANT_NAME")

    # Scheduler
    scheduler_enabled: bool = Field(default=True, alias="SCHEDULER_ENABLED")

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"


settings = Settings()
