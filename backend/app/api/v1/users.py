import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.schemas.base import SuccessResponse
from app.schemas.user import UserPreferencesResponse, UserPreferencesUpdate
from app.services.user import UserService

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_user_service(db: AsyncSession = Depends(get_db)) -> UserService:
    return UserService(db)


@router.get("/me/preferences", response_model=SuccessResponse[UserPreferencesResponse])
async def get_my_preferences(
    user: dict = Depends(get_current_user),
    service: UserService = Depends(_get_user_service),
):
    result = await service.get_preferences(user["user_id"], user["tenant_id"])
    return SuccessResponse(data=UserPreferencesResponse(**result))


@router.put("/me/preferences", response_model=SuccessResponse[UserPreferencesResponse])
async def update_my_preferences(
    request: UserPreferencesUpdate,
    user: dict = Depends(get_current_user),
    service: UserService = Depends(_get_user_service),
):
    result = await service.update_preferences(user["user_id"], user["tenant_id"], request.favorite_tags)
    return SuccessResponse(data=UserPreferencesResponse(**result))
