import re
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field, field_validator

T = TypeVar("T")


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_OAUTH_CODE = "INVALID_OAUTH_CODE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    INVALID_TOKEN = "INVALID_TOKEN"
    INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ADMIN_LOGIN_DISABLED = "ADMIN_LOGIN_DISABLED"
    SSO_PROVIDER_ERROR = "SSO_PROVIDER_ERROR"
    FORBIDDEN = "FORBIDDEN"
    CATEGORY_NOT_FOUND = "CATEGORY_NOT_FOUND"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    SYMBOL_NOT_FOUND = "SYMBOL_NOT_FOUND"
    ITEM_NOT_FOUND = "ITEM_NOT_FOUND"
    DUPLICATE_CATEGORY = "DUPLICATE_CATEGORY"
    DUPLICATE_WATCHLIST_ITEM = "DUPLICATE_WATCHLIST_ITEM"
    NO_COLLECTOR_AVAILABLE = "NO_COLLECTOR_AVAILABLE"
    RATE_LIMIT_EXCEEDED = "RATE_LIMIT_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: list[dict[str, str]] | None = None


class PaginatedMeta(BaseModel):
    total: int = Field(default=0)
    page: int = Field(default=1)
    page_size: int = Field(default=20)


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = Field(default=True)
    data: T
    meta: PaginatedMeta | None = None


class ErrorResponse(BaseModel):
    success: bool = Field(default=False)
    error: ErrorDetail


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = Field(default=True)
    data: list[T]
    meta: PaginatedMeta


class PaginationParams(BaseModel):
    """Common pagination + sorting query params for list endpoints.

    Consume as a FastAPI dependency (`pagination: PaginationParams = Depends()`);
    every field becomes a query parameter. `sort_by` defaults to None so each
    endpoint keeps its own default ordering; when provided it is validated against
    the endpoint's column whitelist by `app.core.pagination.apply_sort` (unknown
    columns raise 400 VALIDATION_ERROR — only whitelisted ORM attributes may be
    ordered by).
    """

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: str | None = Field(default=None, description="Column to sort by (endpoint-specific whitelist)")
    sort_order: Literal["asc", "desc"] = Field(default="desc")

    @field_validator("sort_by")
    @classmethod
    def _validate_sort_by(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
            raise ValueError("sort_by must be a valid column identifier")
        return value
