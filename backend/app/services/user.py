import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import InvalidToken, ValidationError
from app.models.user import User
from app.schemas.user import FAVORITE_TAG_PATTERN, MAX_FAVORITE_TAGS

logger = logging.getLogger(__name__)

_favorite_tag_re = re.compile(FAVORITE_TAG_PATTERN)


def normalize_favorite_tags(raw_tags: list[str]) -> list[str]:
    """Lowercase, validate and de-duplicate favorite tags (first-seen order kept).

    Raises ValidationError (400 VALIDATION_ERROR) on any malformed tag or when the
    de-duplicated list exceeds MAX_FAVORITE_TAGS.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []

    for raw in raw_tags:
        tag = raw.lower()
        if not _favorite_tag_re.match(tag):
            invalid.append(raw)
            continue
        if tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)

    details: list[dict] = []
    if invalid:
        details.append(
            {
                "field": "favorite_tags",
                "message": f"Tags must match {FAVORITE_TAG_PATTERN}: {', '.join(invalid[:10])}",
            }
        )
    if len(normalized) > MAX_FAVORITE_TAGS:
        details.append(
            {
                "field": "favorite_tags",
                "message": f"At most {MAX_FAVORITE_TAGS} favorite tags are allowed",
            }
        )
    if details:
        raise ValidationError(message="Invalid favorite_tags", details=details)

    return normalized


class UserService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_preferences(self, user_id: str, tenant_id: str) -> dict:
        user = await self._get_user(user_id, tenant_id)
        return {"favorite_tags": self._read_favorite_tags(user.preferences)}

    async def update_preferences(self, user_id: str, tenant_id: str, favorite_tags: list[str]) -> dict:
        tags = normalize_favorite_tags(favorite_tags)
        user = await self._get_user(user_id, tenant_id)

        # Merge instead of replace: preferences may carry other keys in the future.
        preferences = dict(user.preferences or {})
        preferences["favorite_tags"] = tags
        user.preferences = preferences
        await self.db.commit()
        await self.db.refresh(user)

        return {"favorite_tags": tags}

    async def get_user_preferences(self, user_id: str, tenant_id: str) -> dict | None:
        """Raw preferences blob for personalization (e.g. tech relevance ranking).

        Returns None instead of raising when the user row is missing/stale: the tech
        feed must keep working for tokens whose user record is gone.
        """
        user = await self._find_user(user_id, tenant_id)
        return user.preferences if user else None

    @staticmethod
    def _read_favorite_tags(preferences: dict | None) -> list[str]:
        if not preferences:
            return []
        tags = preferences.get("favorite_tags")
        if not isinstance(tags, list):
            return []
        return [tag for tag in tags if isinstance(tag, str)]

    async def _get_user(self, user_id: str, tenant_id: str) -> User:
        user = await self._find_user(user_id, tenant_id)
        if user is None:
            raise InvalidToken(message="User not found")
        return user

    async def _find_user(self, user_id: str, tenant_id: str) -> User | None:
        try:
            user_uuid = uuid.UUID(user_id)
            tenant_uuid = uuid.UUID(tenant_id)
        except (ValueError, TypeError, AttributeError):
            return None

        result = await self.db.execute(
            select(User).where(
                User.id == user_uuid,
                User.tenant_id == tenant_uuid,
            )
        )
        return result.scalar_one_or_none()
