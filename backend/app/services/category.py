import logging
import re

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

# Fix: the local SYSTEM_TENANT_ID used to be the string "system", which asyncpg failed to
# encode when compared against a UUID column. Same fix as source.py: reuse the UUID
# constant from core.constants (keeping the original exported name).
from app.core.constants import SYSTEM_TENANT_ID
from app.core.exceptions import CategoryNotFound, DuplicateCategory, Forbidden, ValidationError
from app.core.pagination import apply_sort
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.schemas.base import PaginatedMeta, PaginatedResponse, SuccessResponse
from app.schemas.category import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CategoryWithSourcesResponse,
    SubCategoryResponse,
)
from app.services.tech import TechService

logger = logging.getLogger(__name__)

SUBCATEGORY_LABEL_MAP = {
    "china-stock": "A股行情",
    "watchlist": "自选关注",
    "search": "股票/基金搜索",
    "market-indices": "世界市场指数",
    "commodities": "大宗商品/期货",
    "fund-nav": "基金NAV估值",
    "humanoid": "人形机器人",
    "industrial": "工业机器人",
    "cobot": "协作机器人",
    "autonomous-driving": "自动驾驶",
    "drone": "无人机",
    "robot-software": "机器人OS/软件",
    "llm": "大语言模型",
    "generative-ai": "生成式AI",
    "ai-hardware": "AI芯片/硬件",
    "ai-ethics": "AI伦理与治理",
    "multimodal": "多模态AI",
    "ai-agent": "AI Agent/应用",
    "iot-edge": "IoT与边缘计算",
    "risc-v": "RISC-V与处理器",
    "rtos": "实时操作系统",
    "fpga": "FPGA与硬件加速",
    "chip-design": "芯片设计",
    "embedded-ai": "嵌入式AI",
    "commercial-space": "商业航天",
    "satellite-internet": "卫星互联网",
    "deep-space": "深空探测",
    "orbital": "空间站与在轨服务",
    "rocket-tech": "火箭技术",
    "space-manufacturing": "太空制造与资源",
}

# Whitelist of columns GET /categories may order by (see apply_sort).
CATEGORY_SORT_FIELDS = {"name", "created_at", "type"}


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


def _category_to_response(category: Category, source_count: int = 0) -> CategoryResponse:
    return CategoryResponse(
        id=str(category.id),
        name=category.name,
        slug=category.slug,
        description=category.description,
        icon=category.icon or "folder",
        color=category.color or "#3B82F6",
        type=category.type,
        refresh_interval_seconds=category.refresh_interval_seconds,
        is_active=category.is_active,
        source_count=source_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


class CategoryService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def list_categories(
        self,
        tenant_id: str,
        type_filter: str | None = None,
        page: int = 1,
        page_size: int = 20,
        sort_by: str | None = None,
        sort_order: str = "desc",
    ) -> PaginatedResponse[CategoryResponse]:
        conditions = [
            or_(
                Category.tenant_id == tenant_id,
                Category.tenant_id == SYSTEM_TENANT_ID,
            )
        ]
        if type_filter:
            conditions.append(Category.type == type_filter)

        count_stmt = select(func.count()).select_from(Category).where(and_(*conditions))
        total = (await self.db.execute(count_stmt)).scalar() or 0

        source_count_sub = (
            select(
                Source.category_id,
                func.count().label("cnt"),
            )
            .group_by(Source.category_id)
            .subquery()
        )

        stmt = (
            select(Category, func.coalesce(source_count_sub.c.cnt, 0).label("source_count"))
            .outerjoin(source_count_sub, Category.id == source_count_sub.c.category_id)
            .where(and_(*conditions))
        )
        if sort_by is not None:
            stmt = apply_sort(stmt, sort_by, CATEGORY_SORT_FIELDS, Category, sort_order)
        else:
            stmt = stmt.order_by(Category.type.asc(), Category.name.asc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        rows = (await self.db.execute(stmt)).all()

        categories = [_category_to_response(row[0], row[1]) for row in rows]

        return PaginatedResponse(
            success=True,
            data=categories,
            meta=PaginatedMeta(total=total, page=page, page_size=page_size),
        )

    async def get_category(self, category_id: str, tenant_id: str) -> SuccessResponse[CategoryResponse]:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if category is None:
            raise CategoryNotFound()

        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison is always unequal and would 404
        # every tenant-owned category.
        if str(category.tenant_id) != tenant_id and category.tenant_id != SYSTEM_TENANT_ID:
            raise CategoryNotFound(message="Category not accessible for this tenant")

        source_count_stmt = select(func.count()).select_from(Source).where(Source.category_id == category_id)
        source_count = (await self.db.execute(source_count_stmt)).scalar() or 0

        return SuccessResponse(
            success=True,
            data=_category_to_response(category, source_count),
        )

    async def create_category(self, data: CategoryCreate, tenant_id: str) -> SuccessResponse[CategoryResponse]:
        tenant_stmt = select(Tenant).where(Tenant.id == tenant_id)
        tenant = (await self.db.execute(tenant_stmt)).scalar_one_or_none()
        if tenant is None:
            raise ValidationError(message="Tenant not found")

        current_count_stmt = select(func.count()).select_from(Category).where(Category.tenant_id == tenant_id)
        current_count = (await self.db.execute(current_count_stmt)).scalar() or 0
        if current_count >= tenant.max_categories:
            raise ValidationError(
                message=f"Category limit reached (max {tenant.max_categories})",
                details=[
                    {
                        "field": "name",
                        "message": f"Tenant already has {current_count} categories (max {tenant.max_categories})",
                    }
                ],
            )

        slug = data.slug or _slugify(data.name)

        duplicate_stmt = select(Category).where(
            and_(
                or_(Category.tenant_id == tenant_id, Category.tenant_id == SYSTEM_TENANT_ID),
                Category.slug == slug,
            )
        )
        existing = (await self.db.execute(duplicate_stmt)).scalar_one_or_none()
        if existing is not None:
            raise DuplicateCategory(message=f"Category with slug '{slug}' already exists")

        keywords = data.keywords_filter or []

        category = Category(
            tenant_id=tenant_id,
            name=data.name,
            slug=slug,
            description=data.description,
            icon=data.icon or "folder",
            color=data.color or "#3B82F6",
            type=data.type,
            refresh_interval_seconds=data.refresh_interval_seconds,
            keywords_filter=keywords,
            is_active=data.is_active,
        )
        self.db.add(category)
        await self.db.flush()
        await self.db.refresh(category)

        return SuccessResponse(
            success=True,
            data=_category_to_response(category, 0),
        )

    async def update_category(
        self,
        category_id: str,
        data: CategoryUpdate,
        tenant_id: str,
    ) -> SuccessResponse[CategoryResponse]:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if category is None:
            raise CategoryNotFound()

        if category.tenant_id == SYSTEM_TENANT_ID:
            raise Forbidden(message="Cannot modify predefined system categories")

        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison would reject every owner update.
        if str(category.tenant_id) != tenant_id:
            raise Forbidden(message="Cannot modify categories from other tenants")

        update_data = data.model_dump(exclude_unset=True)

        if "slug" in update_data and update_data["slug"]:
            new_slug = update_data["slug"]
            dup_stmt = select(Category).where(
                and_(
                    or_(Category.tenant_id == tenant_id, Category.tenant_id == SYSTEM_TENANT_ID),
                    Category.slug == new_slug,
                    Category.id != category_id,
                )
            )
            existing = (await self.db.execute(dup_stmt)).scalar_one_or_none()
            if existing is not None:
                raise DuplicateCategory(message=f"Category with slug '{new_slug}' already exists")

        if "keywords_filter" in update_data and update_data["keywords_filter"] is not None:
            update_data["keywords_filter"] = update_data["keywords_filter"]

        for field, value in update_data.items():
            if value is not None:
                setattr(category, field, value)

        await self.db.flush()
        await self.db.refresh(category)

        source_count_stmt = select(func.count()).select_from(Source).where(Source.category_id == category_id)
        source_count = (await self.db.execute(source_count_stmt)).scalar() or 0

        return SuccessResponse(
            success=True,
            data=_category_to_response(category, source_count),
        )

    async def delete_category(self, category_id: str, tenant_id: str) -> None:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if category is None:
            raise CategoryNotFound()

        if category.tenant_id == SYSTEM_TENANT_ID:
            raise Forbidden(message="Cannot delete predefined system categories")

        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison would reject every owner delete.
        if str(category.tenant_id) != tenant_id:
            raise Forbidden(message="Cannot delete categories from other tenants")

        source_count_stmt = select(func.count()).select_from(Source).where(Source.category_id == category_id)
        source_count = (await self.db.execute(source_count_stmt)).scalar() or 0
        if source_count > 0:
            raise ValidationError(
                message=f"Category has {source_count} active sources. Remove sources before deleting the category.",
                details=[{"field": "category_id", "message": f"Category '{category.name}' has {source_count} sources"}],
            )

        await self.db.delete(category)
        await self.db.flush()

    async def get_predefined_categories(self, tenant_id: str) -> SuccessResponse[list[CategoryResponse]]:
        stmt = select(Category).where(Category.tenant_id == SYSTEM_TENANT_ID).order_by(Category.name.asc())
        result = await self.db.execute(stmt)
        categories = result.scalars().all()

        source_count_sub = (
            select(
                Source.category_id,
                func.count().label("cnt"),
            )
            .group_by(Source.category_id)
            .subquery()
        )

        responses = []
        for cat in categories:
            sc_stmt = select(func.coalesce(source_count_sub.c.cnt, 0)).where(source_count_sub.c.category_id == cat.id)
            sc = (await self.db.execute(sc_stmt)).scalar() or 0
            responses.append(_category_to_response(cat, sc))

        return SuccessResponse(success=True, data=responses)

    async def get_category_with_sources(
        self,
        category_id: str,
        tenant_id: str,
    ) -> SuccessResponse[CategoryWithSourcesResponse]:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if category is None:
            raise CategoryNotFound()

        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison is always unequal and would 404
        # every tenant-owned category.
        if str(category.tenant_id) != tenant_id and category.tenant_id != SYSTEM_TENANT_ID:
            raise CategoryNotFound(message="Category not accessible for this tenant")

        sources_stmt = (
            select(Source)
            .where(Source.category_id == category_id)
            .options(selectinload(Source.health))
            .order_by(Source.priority.asc())
        )
        sources = (await self.db.execute(sources_stmt)).scalars().all()

        source_list = []
        for src in sources:
            health_status = None
            if src.health is not None:
                health_status = src.health.status
            source_list.append(
                {
                    "id": str(src.id),
                    "name": src.name,
                    "source_type": src.source_type,
                    "url": src.url,
                    "is_active": src.is_active,
                    "priority": src.priority,
                    "health_status": health_status,
                    "refresh_interval_seconds": src.refresh_interval_seconds,
                }
            )

        return SuccessResponse(
            success=True,
            data=CategoryWithSourcesResponse(
                id=str(category.id),
                name=category.name,
                slug=category.slug,
                description=category.description,
                icon=category.icon or "folder",
                color=category.color or "#3B82F6",
                type=category.type,
                refresh_interval_seconds=category.refresh_interval_seconds,
                is_active=category.is_active,
                source_count=len(source_list),
                sources=source_list,
                created_at=category.created_at,
                updated_at=category.updated_at,
            ),
        )

    async def list_category_items(
        self,
        category_id: str,
        tenant_id: str,
        sort: str = "time",
        page: int = 1,
        page_size: int = 20,
        since: str | None = None,
    ) -> dict:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if category is None:
            raise CategoryNotFound()

        # Cross-tenant access is reported as 404 (not 403) to avoid leaking the
        # existence of other tenants' categories — same rule as get_category.
        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str.
        if str(category.tenant_id) != tenant_id and category.tenant_id != SYSTEM_TENANT_ID:
            raise CategoryNotFound(message="Category not accessible for this tenant")

        tech_service = TechService(db=self.db, redis=self.redis)
        return await tech_service.list_category_items(
            category_id=category.id,
            tenant_id=tenant_id,
            sort=sort,
            page=page,
            page_size=page_size,
            since=since,
        )

    async def list_subcategories(
        self,
        category_id: str,
        tenant_id: str,
    ) -> SuccessResponse[list[SubCategoryResponse]]:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if category is None:
            raise CategoryNotFound()

        # str() on both sides: category.tenant_id is a UUID ORM attribute, the JWT
        # tenant id is a str — a direct comparison is always unequal and would 404
        # every tenant-owned category.
        if str(category.tenant_id) != tenant_id and category.tenant_id != SYSTEM_TENANT_ID:
            raise CategoryNotFound(message="Category not accessible for this tenant")

        tag_count_stmt = (
            select(
                func.jsonb_array_elements_text(Item.topic_tags).label("tag"),
                func.count().label("cnt"),
            )
            .where(Item.category_id == category_id)
            .group_by("tag")
            .order_by(func.count().desc())
        )
        tag_rows = (await self.db.execute(tag_count_stmt)).all()

        subcategories = []
        seen = set()
        for row in tag_rows:
            tag = row[0]
            count = row[1]
            if tag in seen:
                continue
            seen.add(tag)

            is_l2 = (
                (
                    category.slug == "finance"
                    and tag in ("china-stock", "watchlist", "search", "market-indices", "commodities", "fund-nav")
                )
                or (
                    category.slug == "tech"
                    and tag
                    in (
                        "humanoid",
                        "industrial",
                        "cobot",
                        "autonomous-driving",
                        "drone",
                        "robot-software",
                        "llm",
                        "generative-ai",
                        "ai-hardware",
                        "ai-ethics",
                        "multimodal",
                        "ai-agent",
                        "iot-edge",
                        "risc-v",
                        "rtos",
                        "fpga",
                        "chip-design",
                        "embedded-ai",
                        "commercial-space",
                        "satellite-internet",
                        "deep-space",
                        "orbital",
                        "rocket-tech",
                        "space-manufacturing",
                    )
                )
                or (category.slug != "finance" and category.slug != "tech")
            )

            if tag in (category.slug, "finance", "tech", "robotics", "ai", "embedded", "space"):
                continue

            if is_l2:
                subcategories.append(
                    SubCategoryResponse(
                        tag=tag,
                        label=SUBCATEGORY_LABEL_MAP.get(tag, tag),
                        count=count,
                    )
                )

        return SuccessResponse(success=True, data=subcategories)
