"""Unit tests for local admin login configuration (docs/dev-guide/design/admin-login.md)."""

import pytest

from app.config import Settings, apply_admin_password_policy
from app.core.security import verify_password


def _settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "ENV": "development"}
    kwargs.update(overrides)
    return Settings(**kwargs)


class TestAdminLoginEnabled:
    def test_default_disabled(self):
        s = _settings()
        assert s.admin_email is None
        assert s.admin_password_hash is None
        assert s.admin_login_enabled is False

    def test_enabled_when_both_set(self):
        s = _settings(ADMIN_EMAIL="admin@example.com", ADMIN_PASSWORD_HASH="$2b$12$dummyhashvalue")
        assert s.admin_login_enabled is True

    def test_disabled_when_only_email(self):
        s = _settings(ADMIN_EMAIL="admin@example.com")
        assert s.admin_login_enabled is False

    def test_disabled_when_only_hash(self):
        s = _settings(ADMIN_PASSWORD_HASH="$2b$12$dummyhashvalue")
        assert s.admin_login_enabled is False

    def test_blank_email_normalized_and_disabled(self):
        s = _settings(ADMIN_EMAIL="   ", ADMIN_PASSWORD_HASH="$2b$12$dummyhashvalue")
        assert s.admin_email is None
        assert s.admin_login_enabled is False

    def test_blank_hash_normalized_and_disabled(self):
        s = _settings(ADMIN_EMAIL="admin@example.com", ADMIN_PASSWORD_HASH="  ")
        assert s.admin_password_hash is None
        assert s.admin_login_enabled is False


class TestAdminPasswordPolicy:
    def test_non_production_hashes_plaintext(self):
        s = _settings(ENV="development", ADMIN_EMAIL="admin@example.com", ADMIN_PASSWORD="s3cret-value")
        apply_admin_password_policy(s)
        assert s.admin_password is None
        assert s.admin_password_hash is not None
        assert s.admin_password_hash.startswith("$2")
        assert verify_password("s3cret-value", s.admin_password_hash) is True
        assert s.admin_login_enabled is True

    def test_production_ignores_plaintext(self):
        s = _settings(ENV="production", ADMIN_EMAIL="admin@example.com", ADMIN_PASSWORD="s3cret-value")
        apply_admin_password_policy(s)
        assert s.admin_password is None
        assert s.admin_password_hash is None
        assert s.admin_login_enabled is False

    def test_production_prefers_existing_hash_over_plaintext(self):
        s = _settings(
            ENV="production",
            ADMIN_EMAIL="admin@example.com",
            ADMIN_PASSWORD_HASH="$2b$12$dummyhashvalue",
            ADMIN_PASSWORD="s3cret-value",
        )
        apply_admin_password_policy(s)
        assert s.admin_password is None
        assert s.admin_password_hash == "$2b$12$dummyhashvalue"
        assert s.admin_login_enabled is True

    def test_no_password_is_noop(self):
        s = _settings(ADMIN_EMAIL="admin@example.com", ADMIN_PASSWORD_HASH="$2b$12$dummyhashvalue")
        apply_admin_password_policy(s)
        assert s.admin_password_hash == "$2b$12$dummyhashvalue"
        assert s.admin_login_enabled is True

    def test_non_production_hash_only_runs_once_per_settings_instance(self):
        # Calling the policy twice must not re-hash (password already cleared).
        s = _settings(ENV="development", ADMIN_EMAIL="admin@example.com", ADMIN_PASSWORD="s3cret-value")
        apply_admin_password_policy(s)
        first = s.admin_password_hash
        apply_admin_password_policy(s)
        assert s.admin_password_hash == first
