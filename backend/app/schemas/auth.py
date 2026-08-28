from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field


class SSOLoginRequest(BaseModel):
    code: str
    redirect_uri: str
    # OAuth CSRF token issued by GET /auth/sso/{provider}/authorize (security.md §3.2).
    # Optional at the schema level so its absence surfaces as 400 VALIDATION_ERROR from
    # the service (matching the other state failures) instead of a 422; values longer
    # than anything we ever issue are rejected here.
    state: str | None = Field(default=None, max_length=256)


class AdminLoginRequest(BaseModel):
    email: EmailStr
    # bcrypt only uses the first 72 bytes; reject longer input at the schema level.
    password: str = Field(max_length=72)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = Field(default="Bearer")
    expires_in: int
    user: UserResponse | None = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    expires_in: int


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    avatar_url: str | None = None
    tenant_id: str
    role: str
    sso_provider: str


class LogoutResponse(BaseModel):
    message: str = Field(default="Logged out")
