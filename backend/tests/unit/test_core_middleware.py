import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.middleware import (
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    _error_count,
    _request_log,
    _total_request_count,
    _total_response_time_ms,
    setup_cors,
    setup_middlewares,
)


class TestSetupCors:
    def test_calls_add_middleware(self):
        app = MagicMock()
        setup_cors(app)
        app.add_middleware.assert_called_once()
        call_args = app.add_middleware.call_args
        from fastapi.middleware.cors import CORSMiddleware

        assert call_args[0][0] == CORSMiddleware
        assert call_args[1]["allow_credentials"] is True
        assert "GET" in call_args[1]["allow_methods"]
        assert "Authorization" in call_args[1]["allow_headers"]


class TestRequestLoggingMiddleware:
    def test_skip_paths(self):
        assert "/api/v1/health" in RequestLoggingMiddleware.SKIP_PATHS
        assert "/api/v1/stream" in RequestLoggingMiddleware.SKIP_PATHS

    async def test_dispatch_normal_request(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/categories"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.1]),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
            patch("app.core.middleware.redis_set", new_callable=AsyncMock),
        ):
            mock_get.return_value = None
            result = await middleware.dispatch(request, call_next)
            assert result == response
            call_next.assert_called_once()

    async def test_dispatch_skip_path(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/health"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)
        assert result == response

    async def test_dispatch_error_status(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "POST"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 500

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.1]),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
            patch("app.core.middleware.redis_set", new_callable=AsyncMock),
        ):
            mock_get.return_value = None
            result = await middleware.dispatch(request, call_next)
            assert result == response

    async def test_dispatch_with_existing_valid_metrics(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/categories"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
            patch("app.core.middleware.redis_set", new_callable=AsyncMock),
        ):
            mock_get.return_value = '{"request_count_total": 5}'
            result = await middleware.dispatch(request, call_next)
            assert result == response
            mock_get.assert_called()

    async def test_dispatch_with_invalid_json_metrics(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/categories"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
            patch("app.core.middleware.redis_set", new_callable=AsyncMock),
        ):
            mock_get.return_value = "not_json"
            result = await middleware.dispatch(request, call_next)
            assert result == response

    async def test_dispatch_milestone_logging(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        import app.core.middleware as mw

        old_count = mw._total_request_count
        mw._total_request_count = 999

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
            patch("app.core.middleware.time.time", return_value=1700000000),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
            patch("app.core.middleware.redis_set", new_callable=AsyncMock),
            patch("app.core.middleware.redis_hset", new_callable=AsyncMock) as mock_hset,
        ):
            mock_get.return_value = None
            result = await middleware.dispatch(request, call_next)
            assert result == response
            assert mock_hset.called

        mw._total_request_count = old_count

    async def test_dispatch_redis_exception_handled(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
        ):
            mock_get.side_effect = Exception("Redis down")
            result = await middleware.dispatch(request, call_next)
            assert result == response

    async def test_dispatch_slow_request_skips_metrics(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200

        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 61.0]),
            patch("app.core.middleware.redis_get", new_callable=AsyncMock) as mock_get,
        ):
            result = await middleware.dispatch(request, call_next)
            assert result == response
            mock_get.assert_not_called()


class TestRateLimitMiddleware:
    async def test_dispatch_pass_through(self):
        middleware = RateLimitMiddleware(app=MagicMock())

        request = MagicMock()
        response = MagicMock()
        call_next = AsyncMock(return_value=response)

        result = await middleware.dispatch(request, call_next)
        assert result == response
        call_next.assert_called_once()


class TestSetupMiddlewares:
    def test_calls_setup_cors_and_adds_logging_middleware(self):
        app = MagicMock()
        setup_middlewares(app)
        call_list = [call[0][0].__name__ for call in app.add_middleware.call_args_list]
        assert "RequestLoggingMiddleware" in call_list or app.add_middleware.call_count == 2


class TestGlobalCounters:
    def test_request_log_deque(self):
        from collections import deque

        assert isinstance(_request_log, deque)
        assert _request_log.maxlen == 1000

    def test_error_count_is_int(self):
        assert isinstance(_error_count, int)

    def test_total_request_count_is_int(self):
        assert isinstance(_total_request_count, int)

    def test_total_response_time_is_float(self):
        assert isinstance(_total_response_time_ms, float)
