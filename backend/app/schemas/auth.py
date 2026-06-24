from __future__ import annotations

from pydantic import BaseModel, Field


class SSOLoginRequest(BaseModel):
    code: str
    redirect_uri: str


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
