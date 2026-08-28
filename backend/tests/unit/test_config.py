import pytest

from app.config import Settings, _parse_list_str


class TestParseListStr:
    def test_none(self):
        result = _parse_list_str(None)
        assert result == []

    def test_empty_string(self):
        result = _parse_list_str("")
        assert result == []

    def test_whitespace_only(self):
        result = _parse_list_str("   ")
        assert result == []

    def test_list_passthrough(self):
        result = _parse_list_str(["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_json_array(self):
        result = _parse_list_str('["a", "b", "c"]')
        assert result == ["a", "b", "c"]

    def test_json_array_with_numbers(self):
        result = _parse_list_str("[1, 2, 3]")
        assert result == ["1", "2", "3"]

    def test_comma_separated(self):
        result = _parse_list_str("a,b,c")
        assert result == ["a", "b", "c"]

    def test_comma_separated_with_spaces(self):
        result = _parse_list_str("a, b, c")
        assert result == ["a", "b", "c"]

    def test_invalid_json_falls_back_to_comma(self):
        result = _parse_list_str("[invalid json")
        assert result == ["[invalid json"]


class TestSettings:
    def test_default_env(self):
        settings = Settings()
        assert settings.env == "development"

    def test_is_production_false(self):
        s = Settings(ENV="development", _env_file=None)
        assert s.is_production is False

    def test_is_production_true(self):
        s = Settings(ENV="production", _env_file=None)
        assert s.is_production is True

    def test_is_development_true(self):
        s = Settings(ENV="development", _env_file=None)
        assert s.is_development is True

    def test_is_development_false(self):
        s = Settings(ENV="production", _env_file=None)
        assert s.is_development is False

    def test_default_log_level(self):
        settings = Settings()
        assert settings.log_level == "INFO"

    def test_default_database_url(self):
        settings = Settings()
        assert "postgresql" in settings.database_url or "sqlite" in settings.database_url

    def test_default_redis_url(self):
        settings = Settings()
        assert settings.redis_url == "redis://localhost:6379/0"

    def test_default_jwt_secret(self):
        settings = Settings()
        assert settings.jwt_secret == "change-this-in-production"

    def test_default_jwt_algorithm(self):
        settings = Settings()
        assert settings.jwt_algorithm == "HS256"

    def test_default_access_token_expire_minutes(self):
        settings = Settings()
        assert settings.jwt_access_token_expire_minutes == 60

    def test_default_refresh_token_expire_days(self):
        settings = Settings()
        assert settings.jwt_refresh_token_expire_days == 7

    def test_sso_google_defaults(self):
        settings = Settings()
        assert settings.google_oauth_client_id is None
        assert settings.google_oauth_client_secret is None

    def test_sso_azure_ad_defaults(self):
        settings = Settings()
        assert settings.azure_ad_client_id is None
        assert settings.azure_ad_client_secret is None
        assert settings.azure_ad_tenant_id == "common"

    def test_sso_github_defaults(self):
        settings = Settings()
        assert settings.github_oauth_client_id is None
        assert settings.github_oauth_client_secret is None

    def test_sso_apple_defaults(self):
        settings = Settings()
        assert settings.apple_client_id is None
        assert settings.apple_team_id is None
        assert settings.apple_key_id is None
        assert settings.apple_private_key_path is None

    def test_sso_facebook_defaults(self):
        settings = Settings()
        assert settings.facebook_app_id is None
        assert settings.facebook_app_secret is None

    def test_default_cors_origins(self):
        settings = Settings()
        assert settings.cors_origins == ["http://localhost:3000", "http://localhost:8000"]

    def test_cors_origins_from_comma_string(self):
        s = Settings(CORS_ORIGINS="http://a.com,http://b.com", _env_file=None)
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_cors_origins_from_json(self):
        s = Settings(CORS_ORIGINS='["http://a.com", "http://b.com"]', _env_file=None)
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_default_sse_heartbeat_interval(self):
        settings = Settings()
        assert settings.sse_heartbeat_interval == 30

    def test_default_rate_limit_enabled(self):
        settings = Settings()
        assert settings.rate_limit_enabled is True

    def test_default_rate_limit_per_minute(self):
        settings = Settings()
        assert settings.rate_limit_per_minute == 60

    def test_default_rate_limit_auth_per_minute(self):
        settings = Settings()
        assert settings.rate_limit_auth_per_minute == 10

    def test_default_rate_limit_search_per_minute(self):
        settings = Settings()
        assert settings.rate_limit_search_per_minute == 20

    def test_default_rate_limit_burst(self):
        settings = Settings()
        assert settings.rate_limit_burst == 10

    def test_rate_limit_env_overrides(self):
        s = Settings(
            RATE_LIMIT_ENABLED="false",
            RATE_LIMIT_PER_MINUTE="120",
            RATE_LIMIT_AUTH_PER_MINUTE="5",
            RATE_LIMIT_SEARCH_PER_MINUTE="15",
            RATE_LIMIT_BURST="3",
            _env_file=None,
        )
        assert s.rate_limit_enabled is False
        assert s.rate_limit_per_minute == 120
        assert s.rate_limit_auth_per_minute == 5
        assert s.rate_limit_search_per_minute == 15
        assert s.rate_limit_burst == 3

    def test_default_tenant_slug(self):
        settings = Settings()
        assert settings.default_tenant_slug == "default"

    def test_default_tenant_name(self):
        settings = Settings()
        assert settings.default_tenant_name == "Default Tenant"

    def test_default_scheduler_enabled(self):
        settings = Settings()
        assert settings.scheduler_enabled is True

    def test_default_database_pool_size(self):
        settings = Settings()
        assert settings.database_pool_size == 20

    def test_default_database_max_overflow(self):
        settings = Settings()
        assert settings.database_max_overflow == 10

    def test_default_database_pool_recycle(self):
        settings = Settings()
        assert settings.database_pool_recycle == 3600

    # --- ENABLED_SSO_PROVIDERS tests ---

    def test_enabled_sso_providers_default(self):
        """Default enabled_sso_providers includes only google and github."""
        settings = Settings()
        assert settings.enabled_sso_providers == ["google", "github"]

    def test_enabled_sso_providers_custom_comma_separated(self):
        """ENABLED_SSO_PROVIDERS parsed from a comma-separated string."""
        s = Settings(ENABLED_SSO_PROVIDERS="google,github,azure_ad", _env_file=None)
        assert s.enabled_sso_providers == ["google", "github", "azure_ad"]

    def test_enabled_sso_providers_custom_with_all(self):
        """All five supported providers can be enabled."""
        s = Settings(
            ENABLED_SSO_PROVIDERS="google,azure_ad,github,apple,facebook",
            _env_file=None,
        )
        assert s.enabled_sso_providers == ["google", "azure_ad", "github", "apple", "facebook"]

    def test_enabled_sso_providers_empty_string(self):
        """Empty string produces an empty list (all providers disabled)."""
        s = Settings(ENABLED_SSO_PROVIDERS="", _env_file=None)
        assert s.enabled_sso_providers == []

    def test_enabled_sso_providers_with_spaces(self):
        """Whitespace around provider names is stripped."""
        s = Settings(ENABLED_SSO_PROVIDERS=" google , github , azure_ad ", _env_file=None)
        assert s.enabled_sso_providers == ["google", "github", "azure_ad"]

    def test_enabled_sso_providers_json_array(self):
        """ENABLED_SSO_PROVIDERS can be a JSON array string."""
        s = Settings(ENABLED_SSO_PROVIDERS='["google","apple"]', _env_file=None)
        assert s.enabled_sso_providers == ["google", "apple"]
