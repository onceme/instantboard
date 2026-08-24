"""Unit tests for the JWT secret startup policy (validate_jwt_secret_policy).

Audit follow-up: the app used to boot silently in production with an 18-char weak
secret or the shipped placeholder default. Staging/production must now fail fast.
"""

import logging

import pytest

from app.config import (
    JWT_SECRET_MIN_LENGTH,
    Settings,
    validate_jwt_secret_policy,
)

STRONG_SECRET = "9f3c4e4a1b2d4f6e8a0c2b4d6f8e0a2c4b6d8f0e2a4c6b8d"  # 48 hex chars
PLACEHOLDER_EXAMPLE = "change-this-in-production-use-a-strong-random-key"


def _settings(**overrides) -> Settings:
    kwargs = {"_env_file": None, "ENV": "development"}
    kwargs.update(overrides)
    return Settings(**kwargs)


class TestProductionFailsFast:
    def test_production_placeholder_default_raises(self):
        s = _settings(ENV="production")
        with pytest.raises(RuntimeError, match="unsafe JWT_SECRET"):
            validate_jwt_secret_policy(s)

    def test_production_short_secret_raises(self):
        s = _settings(ENV="production", JWT_SECRET="eighteen-chars-abc")
        assert len("eighteen-chars-abc") < JWT_SECRET_MIN_LENGTH
        with pytest.raises(RuntimeError, match="unsafe JWT_SECRET"):
            validate_jwt_secret_policy(s)

    def test_production_long_placeholder_from_env_example_raises(self):
        # Longer than the minimum but still a shipped placeholder.
        assert len(PLACEHOLDER_EXAMPLE) >= JWT_SECRET_MIN_LENGTH
        s = _settings(ENV="production", JWT_SECRET=PLACEHOLDER_EXAMPLE)
        with pytest.raises(RuntimeError, match="unsafe JWT_SECRET"):
            validate_jwt_secret_policy(s)

    def test_production_boundary_length_minus_one_raises(self):
        s = _settings(ENV="production", JWT_SECRET="x" * (JWT_SECRET_MIN_LENGTH - 1))
        with pytest.raises(RuntimeError, match="unsafe JWT_SECRET"):
            validate_jwt_secret_policy(s)

    def test_staging_weak_secret_raises(self):
        s = _settings(ENV="staging")
        with pytest.raises(RuntimeError, match="unsafe JWT_SECRET"):
            validate_jwt_secret_policy(s)

    def test_error_message_explains_requirement_without_leaking_secret(self):
        weak = "eighteen-chars-abc"
        s = _settings(ENV="production", JWT_SECRET=weak)
        with pytest.raises(RuntimeError) as excinfo:
            validate_jwt_secret_policy(s)
        message = str(excinfo.value)
        assert weak not in message
        assert str(JWT_SECRET_MIN_LENGTH) in message

    def test_env_value_is_normalized(self):
        s = _settings(ENV="Production")
        with pytest.raises(RuntimeError, match="unsafe JWT_SECRET"):
            validate_jwt_secret_policy(s)


class TestProductionStrongSecretPasses:
    def test_production_strong_secret_passes(self):
        s = _settings(ENV="production", JWT_SECRET=STRONG_SECRET)
        validate_jwt_secret_policy(s)  # must not raise

    def test_staging_strong_secret_passes(self):
        s = _settings(ENV="staging", JWT_SECRET=STRONG_SECRET)
        validate_jwt_secret_policy(s)  # must not raise

    def test_production_boundary_minimum_length_passes(self):
        s = _settings(ENV="production", JWT_SECRET="x" * JWT_SECRET_MIN_LENGTH)
        validate_jwt_secret_policy(s)  # must not raise


class TestNonProductionWarnsOnly:
    def test_development_weak_secret_warns_but_does_not_raise(self, caplog):
        s = _settings(ENV="development")
        with caplog.at_level(logging.WARNING, logger="app.config"):
            validate_jwt_secret_policy(s)  # must not raise
        assert any("Unsafe JWT_SECRET" in record.message for record in caplog.records)

    def test_test_env_weak_secret_warns_but_does_not_raise(self, caplog):
        s = _settings(ENV="test")
        with caplog.at_level(logging.WARNING, logger="app.config"):
            validate_jwt_secret_policy(s)  # must not raise
        assert any("Unsafe JWT_SECRET" in record.message for record in caplog.records)

    def test_development_strong_secret_is_silent(self, caplog):
        s = _settings(ENV="development", JWT_SECRET=STRONG_SECRET)
        with caplog.at_level(logging.WARNING, logger="app.config"):
            validate_jwt_secret_policy(s)
        assert not any("Unsafe JWT_SECRET" in record.message for record in caplog.records)
