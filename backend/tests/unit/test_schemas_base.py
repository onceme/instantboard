import pytest
from pydantic import ValidationError

from app.schemas.base import (
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
    PaginatedMeta,
    PaginatedResponse,
    PaginationParams,
    SuccessResponse,
)


class TestErrorCode:
    def test_validation_error(self):
        assert ErrorCode.VALIDATION_ERROR == "VALIDATION_ERROR"

    def test_invalid_oauth_code(self):
        assert ErrorCode.INVALID_OAUTH_CODE == "INVALID_OAUTH_CODE"

    def test_auth_required(self):
        assert ErrorCode.AUTH_REQUIRED == "AUTH_REQUIRED"

    def test_invalid_token(self):
        assert ErrorCode.INVALID_TOKEN == "INVALID_TOKEN"

    def test_invalid_refresh_token(self):
        assert ErrorCode.INVALID_REFRESH_TOKEN == "INVALID_REFRESH_TOKEN"

    def test_sso_provider_error(self):
        assert ErrorCode.SSO_PROVIDER_ERROR == "SSO_PROVIDER_ERROR"

    def test_forbidden(self):
        assert ErrorCode.FORBIDDEN == "FORBIDDEN"

    def test_category_not_found(self):
        assert ErrorCode.CATEGORY_NOT_FOUND == "CATEGORY_NOT_FOUND"

    def test_source_not_found(self):
        assert ErrorCode.SOURCE_NOT_FOUND == "SOURCE_NOT_FOUND"

    def test_symbol_not_found(self):
        assert ErrorCode.SYMBOL_NOT_FOUND == "SYMBOL_NOT_FOUND"

    def test_item_not_found(self):
        assert ErrorCode.ITEM_NOT_FOUND == "ITEM_NOT_FOUND"

    def test_duplicate_category(self):
        assert ErrorCode.DUPLICATE_CATEGORY == "DUPLICATE_CATEGORY"

    def test_duplicate_watchlist_item(self):
        assert ErrorCode.DUPLICATE_WATCHLIST_ITEM == "DUPLICATE_WATCHLIST_ITEM"

    def test_rate_limit_exceeded(self):
        assert ErrorCode.RATE_LIMIT_EXCEEDED == "RATE_LIMIT_EXCEEDED"

    def test_internal_error(self):
        assert ErrorCode.INTERNAL_ERROR == "INTERNAL_ERROR"

    def test_service_unavailable(self):
        assert ErrorCode.SERVICE_UNAVAILABLE == "SERVICE_UNAVAILABLE"

    def test_invalid_credentials(self):
        assert ErrorCode.INVALID_CREDENTIALS == "INVALID_CREDENTIALS"

    def test_admin_login_disabled(self):
        assert ErrorCode.ADMIN_LOGIN_DISABLED == "ADMIN_LOGIN_DISABLED"

    def test_all_values_count(self):
        # NO_COLLECTOR_AVAILABLE added for the source-enable collector pre-flight
        assert len(ErrorCode) == 19


class TestErrorDetail:
    def test_basic(self):
        detail = ErrorDetail(code=ErrorCode.VALIDATION_ERROR, message="test error")
        assert detail.code == ErrorCode.VALIDATION_ERROR
        assert detail.message == "test error"
        assert detail.details is None

    def test_with_details(self):
        detail = ErrorDetail(
            code=ErrorCode.VALIDATION_ERROR,
            message="validation failed",
            details=[{"field": "email", "msg": "invalid"}],
        )
        assert detail.details == [{"field": "email", "msg": "invalid"}]


class TestPaginatedMeta:
    def test_defaults(self):
        meta = PaginatedMeta()
        assert meta.total == 0
        assert meta.page == 1
        assert meta.page_size == 20

    def test_custom_values(self):
        meta = PaginatedMeta(total=100, page=2, page_size=10)
        assert meta.total == 100
        assert meta.page == 2
        assert meta.page_size == 10


class TestSuccessResponse:
    def test_basic(self):
        response = SuccessResponse[str](success=True, data="test")
        assert response.success is True
        assert response.data == "test"
        assert response.meta is None

    def test_with_meta(self):
        meta = PaginatedMeta(total=50, page=3, page_size=20)
        response = SuccessResponse[str](success=True, data="test", meta=meta)
        assert response.meta.total == 50

    def test_list_data(self):
        response = SuccessResponse[list[int]](success=True, data=[1, 2, 3])
        assert response.data == [1, 2, 3]


class TestErrorResponse:
    def test_basic(self):
        error = ErrorDetail(code=ErrorCode.AUTH_REQUIRED, message="auth required")
        response = ErrorResponse(error=error)
        assert response.success is False
        assert response.error.code == ErrorCode.AUTH_REQUIRED
        assert response.error.message == "auth required"


class TestPaginatedResponse:
    def test_basic(self):
        meta = PaginatedMeta(total=10, page=1, page_size=5)
        response = PaginatedResponse[str](success=True, data=["a", "b"], meta=meta)
        assert response.success is True
        assert response.data == ["a", "b"]
        assert response.meta.total == 10


class TestPaginationParams:
    def test_defaults(self):
        params = PaginationParams()
        assert params.page == 1
        assert params.page_size == 20
        # sort_by defaults to None so each endpoint keeps its own default ordering
        assert params.sort_by is None
        assert params.sort_order == "desc"

    def test_custom_values(self):
        params = PaginationParams(page=2, page_size=50, sort_by="name", sort_order="asc")
        assert params.page == 2
        assert params.page_size == 50
        assert params.sort_by == "name"
        assert params.sort_order == "asc"

    def test_invalid_page(self):
        with pytest.raises(ValidationError):
            PaginationParams(page=0)

    def test_invalid_page_size_too_large(self):
        with pytest.raises(ValidationError):
            PaginationParams(page_size=101)

    def test_invalid_page_size_zero(self):
        with pytest.raises(ValidationError):
            PaginationParams(page_size=0)

    def test_invalid_sort_order(self):
        with pytest.raises(ValidationError):
            PaginationParams(sort_order="invalid")

    def test_sort_by_none_allowed(self):
        params = PaginationParams(sort_by=None)
        assert params.sort_by is None

    def test_invalid_sort_by_identifier(self):
        # Anything that is not a plain identifier is rejected before the per-endpoint
        # whitelist check (apply_sort) even runs.
        with pytest.raises(ValidationError):
            PaginationParams(sort_by="name; DROP TABLE categories")

        with pytest.raises(ValidationError):
            PaginationParams(sort_by="1name")
