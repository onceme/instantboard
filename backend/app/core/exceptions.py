from fastapi import HTTPException, status

from app.schemas.base import ErrorCode, ErrorDetail, ErrorResponse


class AppException(HTTPException):
    def __init__(self, status_code: int, error_code: ErrorCode, message: str, details: list[dict] | None = None):
        self.error_code = error_code
        self.error_message = message
        self.error_details = details
        error_response = ErrorResponse(
            error=ErrorDetail(
                code=error_code,
                message=message,
                details=details,
            )
        )
        super().__init__(status_code=status_code, detail=error_response.model_dump())


class ValidationError(AppException):
    def __init__(self, message: str = "Request validation failed", details: list[dict] | None = None):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCode.VALIDATION_ERROR,
            message=message,
            details=details,
        )


class NoCollectorAvailable(AppException):
    # Raised when a source is about to be activated (create with is_active=True or an
    # update that flips is_active to True) but no collector can run for it: the
    # source_type has no registered collector and config.library names none either.
    # Without this check the source would be "active" forever without collecting.
    def __init__(self, message: str = "No collector available for this source type"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCode.NO_COLLECTOR_AVAILABLE,
            message=message,
        )


class InvalidOAuthCode(AppException):
    def __init__(self, message: str = "Invalid OAuth authorization code"):
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            error_code=ErrorCode.INVALID_OAUTH_CODE,
            message=message,
        )


class AuthRequired(AppException):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCode.AUTH_REQUIRED,
            message=message,
        )


class InvalidToken(AppException):
    def __init__(self, message: str = "Invalid or expired JWT token"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCode.INVALID_TOKEN,
            message=message,
        )


class InvalidRefreshToken(AppException):
    def __init__(self, message: str = "Invalid refresh token"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCode.INVALID_REFRESH_TOKEN,
            message=message,
        )


class InvalidCredentials(AppException):
    # Unified 401 for local admin login failures (wrong password, unknown email, or
    # active brute-force lock). The message never reveals which reason applied.
    def __init__(self, message: str = "Incorrect email or password"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            error_code=ErrorCode.INVALID_CREDENTIALS,
            message=message,
        )


class AdminLoginDisabled(AppException):
    def __init__(self, message: str = "Admin login is not enabled"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code=ErrorCode.ADMIN_LOGIN_DISABLED,
            message=message,
        )


class SSOProviderError(AppException):
    # Fix: upstream SSO provider failures are gateway/upstream errors and should return
    # 502, not 401. A 401 triggers the frontend axios interceptor to hard-redirect to the
    # login page, misleadingly presenting an upstream outage as an expired session.
    def __init__(self, message: str = "SSO provider returned an error", details: list[dict] | None = None):
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            error_code=ErrorCode.SSO_PROVIDER_ERROR,
            message=message,
            details=details,
        )


class Forbidden(AppException):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            error_code=ErrorCode.FORBIDDEN,
            message=message,
        )


class CategoryNotFound(AppException):
    def __init__(self, message: str = "Category not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.CATEGORY_NOT_FOUND,
            message=message,
        )


class SourceNotFound(AppException):
    def __init__(self, message: str = "Source not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.SOURCE_NOT_FOUND,
            message=message,
        )


class SymbolNotFound(AppException):
    def __init__(self, message: str = "Symbol not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.SYMBOL_NOT_FOUND,
            message=message,
        )


class ItemNotFound(AppException):
    def __init__(self, message: str = "Item not found"):
        super().__init__(
            status_code=status.HTTP_404_NOT_FOUND,
            error_code=ErrorCode.ITEM_NOT_FOUND,
            message=message,
        )


class DuplicateCategory(AppException):
    def __init__(self, message: str = "Category already exists"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code=ErrorCode.DUPLICATE_CATEGORY,
            message=message,
        )


class DuplicateWatchlistItem(AppException):
    def __init__(self, message: str = "Watchlist item already exists"):
        super().__init__(
            status_code=status.HTTP_409_CONFLICT,
            error_code=ErrorCode.DUPLICATE_WATCHLIST_ITEM,
            message=message,
        )


class RateLimitExceeded(AppException):
    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            message=message,
        )


class InternalError(AppException):
    def __init__(self, message: str = "Internal server error"):
        super().__init__(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error_code=ErrorCode.INTERNAL_ERROR,
            message=message,
        )


class ServiceUnavailable(AppException):
    def __init__(self, message: str = "Service unavailable"):
        super().__init__(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            message=message,
        )
