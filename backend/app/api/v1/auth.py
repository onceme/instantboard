import logging

from fastapi import APIRouter, Body, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.dependencies import get_current_user, get_db, get_raw_token, get_redis
from app.schemas.auth import (
    LogoutResponse,
    RefreshTokenRequest,
    RefreshTokenResponse,
    SSOLoginRequest,
    TokenResponse,
    UserResponse,
)
from app.schemas.base import SuccessResponse
from app.services.auth import AuthService

logger = logging.getLogger(__name__)

router = APIRouter()

SUPPORTED_PROVIDERS = ("google", "azure_ad", "github", "apple", "facebook")


@router.post("/sso/{provider}", response_model=SuccessResponse[TokenResponse])
async def sso_login(
    provider: str,
    request: SSOLoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    if provider not in SUPPORTED_PROVIDERS:
        raise ValidationError(
            message=f"Unsupported SSO provider: {provider}",
            details=[{"field": "provider", "message": f"Must be one of: {', '.join(SUPPORTED_PROVIDERS)}"}],
        )
    service = AuthService(db, redis)
    result = await service.sso_login(provider, request.code, request.redirect_uri)
    return SuccessResponse(data=TokenResponse(**result))


@router.post("/refresh", response_model=SuccessResponse[RefreshTokenResponse])
async def refresh_token(
    request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    service = AuthService(db, redis)
    result = await service.refresh_token(request.refresh_token)
    return SuccessResponse(data=RefreshTokenResponse(**result))


@router.get("/me", response_model=SuccessResponse[UserResponse])
async def get_current_user_info(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    service = AuthService(db, redis)
    result = await service.get_current_user(user["user_id"], user["tenant_id"])
    return SuccessResponse(data=UserResponse(**result))


@router.delete("/logout", response_model=SuccessResponse[LogoutResponse])
async def logout(
    user: dict = Depends(get_current_user),
    refresh_token: str | None = Body(default=None, embed=True),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    access_token = get_raw_token()
    service = AuthService(db, redis)
    result = await service.logout(
        access_token=access_token or "",
        refresh_token_str=refresh_token,
        user_id=user.get("user_id"),
    )
    return SuccessResponse(data=LogoutResponse(**result))
