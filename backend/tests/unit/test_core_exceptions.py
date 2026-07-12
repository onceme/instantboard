import pytest
from fastapi import HTTPException, status

from app.core.exceptions import (
    AppException,
    AuthRequired,
    CategoryNotFound,
    DuplicateCategory,
    DuplicateWatchlistItem,
    Forbidden,
    InternalError,
    InvalidOAuthCode,
    InvalidRefreshToken,
    InvalidToken,
    ItemNotFound,
    RateLimitExceeded,
    ServiceUnavailable,
    SSOProviderError,
    SourceNotFound,
    SymbolNotFound,
    ValidationError,
)
from app.schemas.base import ErrorCode


class TestAppException:
    def test_basic(self):
        exc = AppException(
            status_code=400,
            error_code=ErrorCode.VALIDATION_ERROR,
            message="test error",
        )
        assert exc.status_code == 400
        assert exc.error_code == ErrorCode.VALIDATION_ERROR
        assert exc.error_message == "test error"
        assert exc.error_details is None

    def test_with_details(self):
        details = [{"field": "email", "msg": "invalid"}]
        exc = AppException(
            status_code=400,
            error_code=ErrorCode.VALIDATION_ERROR,
            message="validation failed",
            details=details,
        )
        assert exc.error_details == details

    def test_http_exception_inheritance(self):
        exc = AppException(
            status_code=400,
            error_code=ErrorCode.VALIDATION_ERROR,
            message="test",
        )
        assert isinstance(exc, HTTPException)

    def test_detail_dict(self):
        exc = AppException(
            status_code=400,
            error_code=ErrorCode.VALIDATION_ERROR,
            message="test",
        )
        detail = exc.detail
        assert isinstance(detail, dict)
        assert "error" in detail
        assert detail["error"]["code"] == ErrorCode.VALIDATION_ERROR
        assert detail["error"]["message"] == "test"


class TestValidationError:
    def test_default_message(self):
        exc = ValidationError()
        assert exc.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.error_code == ErrorCode.VALIDATION_ERROR
        assert exc.error_message == "Request validation failed"

    def test_custom_message(self):
        exc = ValidationError(message="custom error")
        assert exc.error_message == "custom error"

    def test_with_details(self):
        details = [{"field": "name", "msg": "required"}]
        exc = ValidationError(details=details)
        assert exc.error_details == details


class TestInvalidOAuthCode:
    def test_default(self):
        exc = InvalidOAuthCode()
        assert exc.status_code == status.HTTP_400_BAD_REQUEST
        assert exc.error_code == ErrorCode.INVALID_OAUTH_CODE
        assert exc.error_message == "Invalid OAuth authorization code"

    def test_custom_message(self):
        exc = InvalidOAuthCode(message="custom")
        assert exc.error_message == "custom"


class TestAuthRequired:
    def test_default(self):
        exc = AuthRequired()
        assert exc.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.error_code == ErrorCode.AUTH_REQUIRED
        assert exc.error_message == "Authentication required"

    def test_custom_message(self):
        exc = AuthRequired(message="custom auth")
        assert exc.error_message == "custom auth"


class TestInvalidToken:
    def test_default(self):
        exc = InvalidToken()
        assert exc.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.error_code == ErrorCode.INVALID_TOKEN
        assert exc.error_message == "Invalid or expired JWT token"

    def test_custom_message(self):
        exc = InvalidToken(message="token expired")
        assert exc.error_message == "token expired"


class TestInvalidRefreshToken:
    def test_default(self):
        exc = InvalidRefreshToken()
        assert exc.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.error_code == ErrorCode.INVALID_REFRESH_TOKEN
        assert exc.error_message == "Invalid refresh token"

    def test_custom_message(self):
        exc = InvalidRefreshToken(message="custom")
        assert exc.error_message == "custom"


class TestSSOProviderError:
    def test_default(self):
        exc = SSOProviderError()
        assert exc.status_code == status.HTTP_401_UNAUTHORIZED
        assert exc.error_code == ErrorCode.SSO_PROVIDER_ERROR
        assert exc.error_message == "SSO provider returned an error"

    def test_custom_message(self):
        exc = SSOProviderError(message="provider down")
        assert exc.error_message == "provider down"


class TestForbidden:
    def test_default(self):
        exc = Forbidden()
        assert exc.status_code == status.HTTP_403_FORBIDDEN
        assert exc.error_code == ErrorCode.FORBIDDEN
        assert exc.error_message == "Permission denied"

    def test_custom_message(self):
        exc = Forbidden(message="no access")
        assert exc.error_message == "no access"


class TestCategoryNotFound:
    def test_default(self):
        exc = CategoryNotFound()
        assert exc.status_code == status.HTTP_404_NOT_FOUND
        assert exc.error_code == ErrorCode.CATEGORY_NOT_FOUND
        assert exc.error_message == "Category not found"

    def test_custom_message(self):
        exc = CategoryNotFound(message="cat 123 not found")
        assert exc.error_message == "cat 123 not found"


class TestSourceNotFound:
    def test_default(self):
        exc = SourceNotFound()
        assert exc.status_code == status.HTTP_404_NOT_FOUND
        assert exc.error_code == ErrorCode.SOURCE_NOT_FOUND
        assert exc.error_message == "Source not found"

    def test_custom_message(self):
        exc = SourceNotFound(message="custom")
        assert exc.error_message == "custom"


class TestSymbolNotFound:
    def test_default(self):
        exc = SymbolNotFound()
        assert exc.status_code == status.HTTP_404_NOT_FOUND
        assert exc.error_code == ErrorCode.SYMBOL_NOT_FOUND
        assert exc.error_message == "Symbol not found"

    def test_custom_message(self):
        exc = SymbolNotFound(message="AAPL not found")
        assert exc.error_message == "AAPL not found"


class TestItemNotFound:
    def test_default(self):
        exc = ItemNotFound()
        assert exc.status_code == status.HTTP_404_NOT_FOUND
        assert exc.error_code == ErrorCode.ITEM_NOT_FOUND
        assert exc.error_message == "Item not found"

    def test_custom_message(self):
        exc = ItemNotFound(message="item 456 not found")
        assert exc.error_message == "item 456 not found"


class TestDuplicateCategory:
    def test_default(self):
        exc = DuplicateCategory()
        assert exc.status_code == status.HTTP_409_CONFLICT
        assert exc.error_code == ErrorCode.DUPLICATE_CATEGORY
        assert exc.error_message == "Category already exists"

    def test_custom_message(self):
        exc = DuplicateCategory(message="finance exists")
        assert exc.error_message == "finance exists"


class TestDuplicateWatchlistItem:
    def test_default(self):
        exc = DuplicateWatchlistItem()
        assert exc.status_code == status.HTTP_409_CONFLICT
        assert exc.error_code == ErrorCode.DUPLICATE_WATCHLIST_ITEM
        assert exc.error_message == "Watchlist item already exists"

    def test_custom_message(self):
        exc = DuplicateWatchlistItem(message="already in watchlist")
        assert exc.error_message == "already in watchlist"


class TestRateLimitExceeded:
    def test_default(self):
        exc = RateLimitExceeded()
        assert exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        assert exc.error_code == ErrorCode.RATE_LIMIT_EXCEEDED
        assert exc.error_message == "Rate limit exceeded"

    def test_custom_message(self):
        exc = RateLimitExceeded(message="too many requests")
        assert exc.error_message == "too many requests"


class TestInternalError:
    def test_default(self):
        exc = InternalError()
        assert exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert exc.error_code == ErrorCode.INTERNAL_ERROR
        assert exc.error_message == "Internal server error"

    def test_custom_message(self):
        exc = InternalError(message="db error")
        assert exc.error_message == "db error"


class TestServiceUnavailable:
    def test_default(self):
        exc = ServiceUnavailable()
        assert exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert exc.error_code == ErrorCode.SERVICE_UNAVAILABLE
        assert exc.error_message == "Service unavailable"

    def test_custom_message(self):
        exc = ServiceUnavailable(message="maintenance")
        assert exc.error_message == "maintenance"
