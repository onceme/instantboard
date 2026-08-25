from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_tenant, get_db
from app.schemas.base import SuccessResponse
from app.schemas.item import ItemTagsResponse, TagUpdateRequest
from app.services.item import ItemService

router = APIRouter()


def _get_item_service(db: AsyncSession) -> ItemService:
    return ItemService(db=db)


@router.post("/{item_id}/tags", response_model=SuccessResponse[ItemTagsResponse])
async def add_item_tag(
    item_id: str,
    request: TagUpdateRequest,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_item_service(db)
    result = await service.add_tag(item_id=item_id, tag=request.tag, tenant_id=tenant_id)
    await db.commit()
    return result


@router.delete("/{item_id}/tags/{tag}", response_model=SuccessResponse[ItemTagsResponse])
async def remove_item_tag(
    item_id: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
    tenant_id: str = Depends(get_current_tenant),
):
    service = _get_item_service(db)
    result = await service.remove_tag(item_id=item_id, tag=tag, tenant_id=tenant_id)
    await db.commit()
    return result
