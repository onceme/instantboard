import logging
import re
import uuid as uuid_module

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ItemNotFound, ValidationError
from app.models.item import Item
from app.schemas.base import SuccessResponse
from app.schemas.item import ItemTagsResponse

logger = logging.getLogger(__name__)

# User tags: lowercase letters, digits and hyphens only, 1-32 chars. Kept distinct
# from the collector's tag vocabulary so manual tags are easy to spot in topic_tags.
USER_TAG_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")

# Upper bound on the number of tags a single item may carry after a user addition.
MAX_ITEM_TAGS = 20


class ItemService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _get_tenant_item(self, item_id: str, tenant_id: str) -> Item:
        """Fetch an item owned by the tenant; missing or cross-tenant -> 404.

        Cross-tenant access returns ItemNotFound (not Forbidden) so the existence
        of other tenants' items is never disclosed. System-tenant shared items are
        excluded as well: rewriting a shared row would leak one tenant's manual tags
        to every other tenant (same ownership rule as categories reclassify).
        """
        try:
            uuid_module.UUID(item_id)
        except (ValueError, AttributeError, TypeError) as err:
            raise ItemNotFound() from err

        stmt = select(Item).where(Item.id == item_id)
        item = (await self.db.execute(stmt)).scalar_one_or_none()
        if item is None:
            raise ItemNotFound()

        # str() on both sides: item.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison is always unequal (same pattern
        # as services/category.py).
        if str(item.tenant_id) != tenant_id:
            raise ItemNotFound(message="Item not accessible for this tenant")
        return item

    async def add_tag(self, item_id: str, tag: str, tenant_id: str) -> SuccessResponse[ItemTagsResponse]:
        if not USER_TAG_PATTERN.fullmatch(tag):
            raise ValidationError(
                message="Invalid tag format",
                details=[
                    {
                        "field": "tag",
                        "message": "Tag must be 1-32 chars of lowercase letters, digits or hyphens",
                    }
                ],
            )

        item = await self._get_tenant_item(item_id, tenant_id)

        # Read-modify-write: append only, preserving the existing hierarchy order
        # (system-extracted tags stay first, the user tag lands at the end).
        # Reassigning a new list is required — SQLAlchemy JSONB columns do not
        # detect in-place mutation. Dedupe: an existing tag is a no-op.
        current_tags = list(item.topic_tags or [])
        if tag not in current_tags:
            if len(current_tags) >= MAX_ITEM_TAGS:
                raise ValidationError(
                    message=f"Tag limit reached (max {MAX_ITEM_TAGS} per item)",
                    details=[
                        {
                            "field": "tag",
                            "message": f"Item already has {len(current_tags)} tags (max {MAX_ITEM_TAGS})",
                        }
                    ],
                )
            item.topic_tags = current_tags + [tag]
            await self.db.flush()

        return SuccessResponse(
            success=True,
            data=ItemTagsResponse(item_id=str(item.id), topic_tags=list(item.topic_tags or [])),
        )

    async def remove_tag(self, item_id: str, tag: str, tenant_id: str) -> SuccessResponse[ItemTagsResponse]:
        item = await self._get_tenant_item(item_id, tenant_id)

        current_tags = list(item.topic_tags or [])
        if tag not in current_tags:
            raise ValidationError(
                message="Tag not found on this item",
                details=[{"field": "tag", "message": f"Tag '{tag}' is not in the item's topic_tags"}],
            )

        item.topic_tags = [existing for existing in current_tags if existing != tag]
        await self.db.flush()

        return SuccessResponse(
            success=True,
            data=ItemTagsResponse(item_id=str(item.id), topic_tags=list(item.topic_tags or [])),
        )
