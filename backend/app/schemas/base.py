from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

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
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    sort_by: str = Field(default="created_at")
    sort_order: str = Field(default="desc", pattern="^(asc|desc)$")
