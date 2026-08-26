from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.middleware import (
    MILESTONE_LOG_EVERY,
    SLOW_REQUEST_THRESHOLD_MS,
    RateLimitMiddleware,
    RequestLoggingMiddleware,
    _record_request_stats,
    _status_bucket,
    _total_request_count,
    setup_cors,
    setup_middlewares,
)
from app.core.redis import RedisKeys

# Frozen clock: 1700000000 // 60 = 28333333, so the minute key the writer is
# expected to touch is dashboard:api_metrics:minute:28333333.
FROZEN_NOW = 1700000000
FROZEN_MINUTE = FROZEN_NOW // 60


def _mock_redis_pipeline():
    pipe = MagicMock()
    pipe.hincrby = MagicMock()
    pipe.hincrbyfloat = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, 1, True, 1, True])
    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)
    return client, pipe


def _pipeline_calls(pipe, name):
    return [call.args for call in getattr(pipe, name).call_args_list]


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


class TestStatusBucket:
    @pytest.mark.parametrize(
        "status_code,expected",
        [
            (200, "count_2xx"),
            (204, "count_2xx"),
            (404, "count_4xx"),
            (429, "count_4xx"),
            (500, "count_5xx"),
            (503, "count_5xx"),
            (101, None),
            (301, None),
            (302, None),
        ],
    )
    def test_bucket_mapping(self, status_code, expected):
        assert _status_bucket(status_code) == expected


class TestRecordRequestStats:
    async def test_writes_totals_minute_buckets_in_one_pipeline(self):
        client, pipe = _mock_redis_pipeline()

        with (
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
            patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
        ):
            mock_client.return_value = client
            await _record_request_stats(123.456, 404)

        minute_key = RedisKeys.api_metrics_minute_key(FROZEN_MINUTE)
        assert pipe.hincrby.call_args_list[0].args == (RedisKeys.API_METRICS_TOTALS, "requests_total", 1)
        assert _pipeline_calls(pipe, "hincrby") == [
            (RedisKeys.API_METRICS_TOTALS, "requests_total", 1),
            (minute_key, "count", 1),
            (minute_key, "count_4xx", 1),
        ]
        assert _pipeline_calls(pipe, "hincrbyfloat") == [(minute_key, "latency_ms", 123.456)]
        assert _pipeline_calls(pipe, "expire") == [(minute_key, RedisKeys.API_METRICS_MINUTE_TTL)]
        pipe.execute.assert_awaited_once()

    async def test_2xx_and_5xx_bucket_fields(self):
        for status_code, expected_field in [(200, "count_2xx"), (503, "count_5xx")]:
            client, pipe = _mock_redis_pipeline()
            with (
                patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
                patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
            ):
                mock_client.return_value = client
                await _record_request_stats(10.0, status_code)

            minute_key = RedisKeys.api_metrics_minute_key(FROZEN_MINUTE)
            assert _pipeline_calls(pipe, "hincrby") == [
                (RedisKeys.API_METRICS_TOTALS, "requests_total", 1),
                (minute_key, "count", 1),
                (minute_key, expected_field, 1),
            ]

    async def test_3xx_has_no_status_bucket(self):
        client, pipe = _mock_redis_pipeline()
        with (
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
            patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
        ):
            mock_client.return_value = client
            await _record_request_stats(10.0, 302)

        minute_key = RedisKeys.api_metrics_minute_key(FROZEN_MINUTE)
        assert _pipeline_calls(pipe, "hincrby") == [
            (RedisKeys.API_METRICS_TOTALS, "requests_total", 1),
            (minute_key, "count", 1),
        ]

    async def test_client_failure_is_swallowed(self):
        with patch("app.core.middleware.get_redis_client", new_callable=AsyncMock, side_effect=Exception("Redis down")):
            await _record_request_stats(10.0, 200)

    async def test_pipeline_failure_is_swallowed(self):
        client, pipe = _mock_redis_pipeline()
        pipe.execute = AsyncMock(side_effect=Exception("boom"))
        with (
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock) as mock_client,
            patch("app.core.middleware.time.time", return_value=FROZEN_NOW),
        ):
            mock_client.return_value = client
            await _record_request_stats(10.0, 200)


class TestRequestLoggingMiddleware:
    def test_skip_paths(self):
        assert "/api/v1/health" in RequestLoggingMiddleware.SKIP_PATHS
        assert "/api/v1/stream" in RequestLoggingMiddleware.SKIP_PATHS

    async def test_dispatch_records_stats(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/categories"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.1]),
            patch("app.core.middleware._record_request_stats", new_callable=AsyncMock) as mock_record,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response
        call_next.assert_called_once()
        mock_record.assert_awaited_once()
        duration_ms, status_code = mock_record.call_args.args
        assert duration_ms == pytest.approx(100.0)
        assert status_code == 200

    async def test_dispatch_skip_path_does_not_record(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/health"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.1]),
            patch("app.core.middleware._record_request_stats", new_callable=AsyncMock) as mock_record,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response
        mock_record.assert_not_awaited()

    async def test_dispatch_slow_request_does_not_record(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, SLOW_REQUEST_THRESHOLD_MS / 1000 + 1]),
            patch("app.core.middleware._record_request_stats", new_callable=AsyncMock) as mock_record,
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response
        mock_record.assert_not_awaited()

    async def test_dispatch_redis_failure_does_not_affect_response(self):
        middleware = RequestLoggingMiddleware(app=MagicMock())

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        with (
            patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
            patch("app.core.middleware.get_redis_client", new_callable=AsyncMock, side_effect=Exception("Redis down")),
        ):
            result = await middleware.dispatch(request, call_next)

        assert result == response

    async def test_dispatch_milestone_logging(self, caplog):
        import app.core.middleware as mw

        middleware = RequestLoggingMiddleware(app=MagicMock())
        old_count = mw._total_request_count
        mw._total_request_count = MILESTONE_LOG_EVERY - 1

        request = MagicMock()
        request.method = "GET"
        request.url.path = "/api/v1/test"

        response = MagicMock()
        response.status_code = 200
        call_next = AsyncMock(return_value=response)

        try:
            with (
                patch("app.core.middleware.time.monotonic", side_effect=[0, 0.05]),
                patch("app.core.middleware._record_request_stats", new_callable=AsyncMock),
                caplog.at_level("INFO", logger="instantboard"),
            ):
                result = await middleware.dispatch(request, call_next)

            assert result == response
            assert mw._total_request_count == MILESTONE_LOG_EVERY
            assert any("milestone" in record.message for record in caplog.records)
        finally:
            mw._total_request_count = old_count


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


class TestModuleConstants:
    def test_total_request_count_is_int(self):
        assert isinstance(_total_request_count, int)

    def test_milestone_interval(self):
        assert MILESTONE_LOG_EVERY == 1000
