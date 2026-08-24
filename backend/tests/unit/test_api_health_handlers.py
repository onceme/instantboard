"""Unit tests for app/api/v1/health.py — direct handler tests."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.health import health_check, health_detail


class TestHealthCheck:
    async def test_returns_healthy(self):
        result = await health_check()
        assert result["status"] == "healthy"
        assert result["version"] == "1.0.0"
        assert "timestamp" in result


class TestHealthDetail:
    async def test_all_healthy(self):
        db = AsyncMock()
        redis = AsyncMock()
        redis.ping = AsyncMock(return_value=True)

        result = await health_detail(db=db, redis_client=redis)
        assert result["status"] == "healthy"
        assert result["details"]["postgresql"]["status"] == "healthy"
        assert result["details"]["redis"]["status"] == "healthy"
        assert result["version"] == "1.0.0"
        assert "environment" in result
        assert "timestamp" in result

    async def test_pg_down(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("connection refused"))
        redis = AsyncMock()
        redis.ping = AsyncMock(return_value=True)

        result = await health_detail(db=db, redis_client=redis)
        assert result["status"] == "degraded"
        assert result["details"]["postgresql"]["status"] == "down"
        assert "error" in result["details"]["postgresql"]
        assert result["details"]["redis"]["status"] == "healthy"

    async def test_redis_down(self):
        db = AsyncMock()
        redis = AsyncMock()
        redis.ping = AsyncMock(side_effect=Exception("Redis unavailable"))

        result = await health_detail(db=db, redis_client=redis)
        assert result["status"] == "degraded"
        assert result["details"]["postgresql"]["status"] == "healthy"
        assert result["details"]["redis"]["status"] == "down"
        assert "error" in result["details"]["redis"]

    async def test_both_down(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=Exception("PG down"))
        redis = AsyncMock()
        redis.ping = AsyncMock(side_effect=Exception("Redis down"))

        result = await health_detail(db=db, redis_client=redis)
        assert result["status"] == "degraded"
        assert result["details"]["postgresql"]["status"] == "down"
        assert result["details"]["redis"]["status"] == "down"

    async def test_redis_ping_returns_false(self):
        db = AsyncMock()
        redis = AsyncMock()
        redis.ping = AsyncMock(return_value=False)

        result = await health_detail(db=db, redis_client=redis)
        assert result["details"]["redis"]["status"] == "down"
        assert result["status"] == "degraded"
