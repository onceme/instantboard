import json
import logging
import math
import re
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.constants import SYSTEM_TENANT_ID
from app.core.redis import redis_get, redis_set
from app.models.category import Category
from app.models.item import Item

logger = logging.getLogger(__name__)

HALF_LIFE_SECONDS = 12 * 3600

DOMAIN_LABELS = {
    "robotics": {"label": "机器人", "icon": "🤖"},
    "ai": {"label": "人工智能", "icon": "🧠"},
    "embedded": {"label": "大规模嵌入式", "icon": "⚡"},
    "space": {"label": "太空科技", "icon": "🚀"},
}

VALID_DOMAINS = {"robotics", "ai", "embedded", "space"}

SUBCATEGORY_TO_DOMAIN = {
    "humanoid": "robotics",
    "industrial": "robotics",
    "cobot": "robotics",
    "autonomous-driving": "robotics",
    "drone": "robotics",
    "robot-software": "robotics",
    "llm": "ai",
    "generative-ai": "ai",
    "ai-hardware": "ai",
    "ai-ethics": "ai",
    "multimodal": "ai",
    "ai-agent": "ai",
    "iot-edge": "embedded",
    "risc-v": "embedded",
    "rtos": "embedded",
    "fpga": "embedded",
    "chip-design": "embedded",
    "embedded-ai": "embedded",
    "commercial-space": "space",
    "satellite-internet": "space",
    "deep-space": "space",
    "orbital": "space",
    "rocket-tech": "space",
    "space-manufacturing": "space",
}

TOPIC_TAG_LABELS = {
    "robotics": "机器人",
    "ai": "人工智能",
    "embedded": "大规模嵌入式",
    "space": "太空科技",
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

KEYWORD_TO_TAG = {
    "GPT": ["ai", "llm"],
    "Claude": ["ai", "llm"],
    "Llama": ["ai", "llm"],
    "Gemini": ["ai", "llm"],
    "Sora": ["ai", "generative-ai", "llm"],
    "transformer": ["ai", "llm"],
    "DALL-E": ["ai", "generative-ai"],
    "Midjourney": ["ai", "generative-ai"],
    "NVIDIA": ["ai", "ai-hardware", "embedded", "chip-design"],
    "GPU": ["ai", "ai-hardware"],
    "TPU": ["ai", "ai-hardware"],
    "Groq": ["ai", "ai-hardware"],
    "alignment": ["ai", "ai-ethics"],
    "AI safety": ["ai", "ai-ethics"],
    "multimodal": ["ai", "multimodal"],
    "AutoGPT": ["ai", "ai-agent"],
    "LangChain": ["ai", "ai-agent"],
    "RAG": ["ai", "ai-agent"],
    "Atlas": ["robotics", "humanoid"],
    "Optimus": ["robotics", "humanoid"],
    "Figure": ["robotics", "humanoid"],
    "AMR": ["robotics", "industrial"],
    "UR": ["robotics", "cobot"],
    "Waymo": ["robotics", "autonomous-driving"],
    "Tesla FSD": ["robotics", "autonomous-driving"],
    "eVTOL": ["robotics", "drone"],
    "ROS2": ["robotics", "robot-software"],
    "Isaac Sim": ["robotics", "robot-software"],
    "MQTT": ["embedded", "iot-edge"],
    "TinyML": ["embedded", "embedded-ai", "iot-edge"],
    "RISC-V": ["embedded", "risc-v"],
    "FreeRTOS": ["embedded", "rtos"],
    "Zephyr": ["embedded", "rtos"],
    "FPGA": ["embedded", "fpga"],
    "EDA": ["embedded", "chip-design"],
    "Chiplet": ["embedded", "chip-design"],
    "NPU": ["ai", "ai-hardware", "embedded", "embedded-ai"],
    "Starlink": ["space", "satellite-internet"],
    "Kuiper": ["space", "satellite-internet"],
    "SpaceX": ["space", "commercial-space", "rocket-tech"],
    "Blue Origin": ["space", "commercial-space"],
    "Rocket Lab": ["space", "commercial-space"],
    "Mars": ["space", "deep-space"],
    "ISS": ["space", "orbital"],
    "Tiangong": ["space", "orbital"],
    "ISRU": ["space", "space-manufacturing"],
}


class TechService:
    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def get_news(
        self,
        tenant_id: str,
        domain: str | None = None,
        subcategory: str | None = None,
        sort: str = "hot",
        page: int = 1,
        page_size: int = 20,
        source_id: str | None = None,
        since: str | None = None,
    ) -> dict:
        tech_category = await self._get_tech_category(tenant_id)

        # Previously, when the category was missing, accessing tech_category.id directly
        # raised AttributeError and caused a bare 500. Log a warning and return an empty
        # success envelope instead.
        if tech_category is None:
            logger.warning(f"Tech category not found for tenant {tenant_id}, returning empty news list")
            return {
                "data": [],
                "meta": {
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                },
            }

        stmt = (
            select(Item)
            .options(selectinload(Item.source))
            .where(
                # Collected items belong to the system tenant; include system-tenant rows as
                # well, otherwise regular tenants can never see them.
                Item.tenant_id.in_([tenant_id, SYSTEM_TENANT_ID]),
                Item.category_id == tech_category.id,
            )
        )

        # JSONB containment filters. topic_tags is a JSONB column, so the right operand
        # of @> must be jsonb as well. Routing the filter through the ORM comparator
        # (Item.topic_tags.contains(...)) makes SQLAlchemy type the bound value with the
        # column's JSONB type and emit "@> :param::JSONB", letting asyncpg send a real
        # jsonb value. The previous raw `text(...).bindparams(tag=json.dumps([...]))`
        # typed the operand as varchar, which PostgreSQL rejects ("operator does not
        # exist: jsonb @> character varying") -> every domain/subcategory-filtered
        # /tech/news request 500'd on PostgreSQL. This stays backend-safe because the
        # expression is only ever executed against PostgreSQL (sqlite unit tests mock
        # the session), and the value is a bound parameter, never string-interpolated.
        if domain and domain in VALID_DOMAINS:
            stmt = stmt.where(Item.topic_tags.contains([domain]))

        if subcategory:
            stmt = stmt.where(Item.topic_tags.contains([subcategory]))

        if source_id:
            stmt = stmt.where(Item.source_id == source_id)

        if since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                stmt = stmt.where(Item.published_at >= since_dt)
            except (ValueError, TypeError):
                logger.warning(f"Invalid since parameter: {since}")

        if sort == "time":
            stmt = stmt.order_by(Item.published_at.desc())
        elif sort == "relevance":
            stmt = stmt.order_by(Item.priority.desc())
        else:
            stmt = stmt.order_by(Item.priority.desc(), Item.published_at.desc())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar() or 0

        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size)

        result = await self.db.execute(stmt)
        items = result.scalars().all()

        data = []
        now = datetime.now(UTC)

        for item in items:
            domain_tag = self._extract_domain_tag(item.topic_tags)
            hot_score = self._calculate_hot_score(item, now)
            source_name = None
            if item.source:
                source_name = item.source.name

            data.append(
                {
                    "id": str(item.id),
                    "title": item.title,
                    "summary": item.summary,
                    "url": item.url,
                    "source_name": source_name,
                    "source_id": str(item.source_id),
                    "category_id": str(item.category_id),
                    "topic_tags": item.topic_tags if item.topic_tags else [],
                    "domain_tag": domain_tag,
                    "published_at": item.published_at,
                    "fetched_at": item.fetched_at,
                    "image_url": item.image_url,
                    "priority": item.priority,
                    "extra_data": item.extra_data if item.extra_data else {},
                    "hot_score": round(hot_score, 4),
                }
            )

        if sort == "hot":
            data.sort(key=lambda x: x["hot_score"], reverse=True)

        return {
            "data": data,
            "meta": {
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }

    async def get_topics(self, tenant_id: str, domain: str | None = None) -> list[dict]:
        tech_category = await self._get_tech_category(tenant_id)

        cache_key = f"t:{tenant_id}:tech_topics:{domain or 'all'}"
        cached = await redis_get(cache_key)
        if cached:
            try:
                return json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        since = datetime.now(UTC) - timedelta(days=7)

        # Same missing-category protection as get_news, avoiding an AttributeError (bare
        # 500) from tech_category.id.
        if tech_category is None:
            logger.warning(f"Tech category not found for tenant {tenant_id}, returning empty topics list")
            return []

        # Same as get_news: the topics aggregation must include system-tenant rows too,
        # otherwise regular tenants get empty stats.
        system_tenant_id = str(SYSTEM_TENANT_ID)

        if domain and domain in VALID_DOMAINS:
            tag_filter = json.dumps([domain])
            sql = text("""
                SELECT tag, COUNT(*) as count, MAX(published_at) as last_active_at
                FROM items, jsonb_array_elements_text(topic_tags) AS tag
                WHERE tenant_id IN (:tenant_id, :system_tenant_id)
                  AND category_id = :category_id
                  AND published_at >= :since
                  AND topic_tags @> :domain_tag
                GROUP BY tag
                ORDER BY count DESC
            """)
            result = await self.db.execute(
                sql,
                {
                    "tenant_id": tenant_id,
                    "system_tenant_id": system_tenant_id,
                    "category_id": str(tech_category.id),
                    "since": since,
                    "domain_tag": tag_filter,
                },
            )
        else:
            sql = text("""
                SELECT tag, COUNT(*) as count, MAX(published_at) as last_active_at
                FROM items, jsonb_array_elements_text(topic_tags) AS tag
                WHERE tenant_id IN (:tenant_id, :system_tenant_id)
                  AND category_id = :category_id
                  AND published_at >= :since
                GROUP BY tag
                ORDER BY count DESC
            """)
            result = await self.db.execute(
                sql,
                {
                    "tenant_id": tenant_id,
                    "system_tenant_id": system_tenant_id,
                    "category_id": str(tech_category.id),
                    "since": since,
                },
            )

        topics = []
        for row in result:
            tag = row[0]
            count = row[1]
            last_active = row[2]

            label = TOPIC_TAG_LABELS.get(tag, tag)

            topics.append(
                {
                    "tag": tag,
                    "label": label,
                    "count": count,
                    "last_active_at": last_active.isoformat() if last_active else None,
                }
            )

        await redis_set(cache_key, json.dumps(topics), ex=900)

        return topics

    def extract_topic_tags(self, title: str, summary: str | None = None) -> list[str]:
        content = f"{title} {summary or ''}"
        tags = set()

        for keyword, tag_list in KEYWORD_TO_TAG.items():
            pattern = re.compile(r"\b" + re.escape(keyword) + r"\b", re.IGNORECASE)
            if pattern.search(content):
                for tag in tag_list:
                    tags.add(tag)

        if not tags:
            tags.add("tech")

        result = ["tech"] + sorted([t for t in tags if t != "tech"])
        return result

    def _calculate_hot_score(self, item: Item, now: datetime) -> float:
        if not item.published_at:
            return float(item.priority or 5)

        age_seconds = (now - item.published_at).total_seconds()
        if age_seconds < 0:
            age_seconds = 0

        base_score = float(item.priority or 5)
        decay_factor = math.exp(-age_seconds / HALF_LIFE_SECONDS)
        score = base_score * decay_factor

        if item.extra_data:
            hn_score = item.extra_data.get("hn_score", 0) or 0
            if hn_score:
                score += math.log1p(float(hn_score)) * 0.5

        return score

    def _extract_domain_tag(self, topic_tags: list[str] | None) -> str | None:
        if not topic_tags:
            return None

        for tag in topic_tags:
            if tag in VALID_DOMAINS:
                return tag

        for tag in topic_tags:
            if tag in SUBCATEGORY_TO_DOMAIN:
                return SUBCATEGORY_TO_DOMAIN[tag]

        return None

    async def _get_tech_category(self, tenant_id: str) -> Category:
        stmt = select(Category).where(
            or_(
                and_(Category.tenant_id == tenant_id, Category.slug == "tech"),
                and_(Category.type == "tech"),
            ),
            Category.is_active,
        )
        result = await self.db.execute(stmt)
        category = result.scalar_one_or_none()

        if not category:
            stmt2 = select(Category).where(
                Category.slug == "tech",
                Category.is_active,
            )
            result2 = await self.db.execute(stmt2)
            category = result2.scalar_one_or_none()

        if not category:
            stmt3 = (
                select(Category)
                .where(
                    Category.type == "tech",
                    Category.is_active,
                )
                .limit(1)
            )
            result3 = await self.db.execute(stmt3)
            category = result3.scalar_one_or_none()

        return category
