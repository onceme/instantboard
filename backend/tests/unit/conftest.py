"""Unit test conftest — clears CI env vars so Settings() uses code defaults."""

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove CI-injected env vars so Settings() returns code-level defaults."""
    for var in (
        "ENV",
        "DATABASE_URL",
        "REDIS_URL",
        "JWT_SECRET",
        "SECRET_KEY",
        "SCHEDULER_ENABLED",
        "ENABLED_SSO_PROVIDERS",
        "LOG_LEVEL",
        "CORS_ORIGINS",
    ):
        monkeypatch.delenv(var, raising=False)
    yield
