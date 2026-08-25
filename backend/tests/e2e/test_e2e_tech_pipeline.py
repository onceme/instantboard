"""E2E: tech news pipeline.

Register a fake collector -> drive the real scheduler collection run twice ->
assert dedup (in-batch and cross-run) and persisted items -> GET /tech/news
returns them with keyword-mapped topic_tags -> GET /tech/topics aggregates them.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.collectors import COLLECTOR_REGISTRY
from app.collectors.base import BaseCollector
from app.core.constants import SYSTEM_TENANT_ID
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source, SourceHealth
from app.models.tenant import Tenant
from app.scheduler.manager import scheduler_manager
from tests.conftest import TEST_DATABASE_URL, test_session_factory
from tests.e2e.conftest import bearer, make_e2e_token

pytestmark = pytest.mark.e2e

FAKE_LIBRARY = "e2e_fake_news"


class FakeE2ECollector(BaseCollector):
    """Deterministic in-memory collector standing in for an upstream news feed."""

    max_retries = 1

    async def fetch_data(self, source):
        return list(self._entries())

    async def parse_data(self, raw_data, source):
        return raw_data

    @staticmethod
    def _entries():
        published = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
        return [
            {
                "title": "OpenAI ships GPT-5 with major LLM reasoning gains",
                "url": "https://e2e.example/news/gpt-5",
                "summary": "OpenAI released GPT-5, a large language model focused on reasoning benchmarks.",
                "published_at": published,
                "extra_data": {"hn_score": 120},
            },
            {
                "title": "SpaceX launches another Starlink batch",
                "url": "https://e2e.example/news/starlink",
                "summary": "SpaceX deployed more Starlink satellites to expand satellite internet coverage.",
                "published_at": published,
                "extra_data": {},
            },
            {
                "title": "Atlas headlines the humanoid robot showcase",
                "url": "https://e2e.example/news/atlas",
                "summary": "Boston Dynamics presented Atlas alongside other humanoid robot platforms.",
                "published_at": published,
                "extra_data": {},
            },
            {
                # exact duplicate of the first entry (same title+url) -> dedup must drop it
                "title": "OpenAI ships GPT-5 with major LLM reasoning gains",
                "url": "https://e2e.example/news/gpt-5",
                "summary": "OpenAI released GPT-5, a large language model focused on reasoning benchmarks.",
                "published_at": published,
                "extra_data": {"hn_score": 120},
            },
        ]


async def _ensure_system_tenant(session) -> Tenant:
    tenant = await session.get(Tenant, SYSTEM_TENANT_ID)
    if tenant is not None:
        return tenant
    result = await session.execute(select(Tenant).where(Tenant.slug == "system"))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant
    tenant = Tenant(id=SYSTEM_TENANT_ID, name="System", slug="system", plan="enterprise", settings={})
    session.add(tenant)
    await session.flush()
    return tenant


@pytest_asyncio.fixture
async def tech_env(monkeypatch):
    async with test_session_factory() as session:
        for model in (Item, SourceHealth, Source, Category):
            await session.execute(delete(model))

        tenant = await _ensure_system_tenant(session)
        category = Category(tenant_id=tenant.id, name="科技", slug="tech", type="tech", is_active=True)
        session.add(category)
        await session.flush()

        source = Source(
            tenant_id=tenant.id,
            category_id=category.id,
            name="E2E Fake Feed",
            source_type="api",
            url="https://e2e.example/feed",
            config={"library": FAKE_LIBRARY},
            is_active=True,
            refresh_interval_seconds=300,
        )
        session.add(source)
        await session.commit()
        env = {"tenant_id": str(tenant.id), "source_id": str(source.id)}

    # resolve_collector("api", {"library": FAKE_LIBRARY}) falls back to config.library
    monkeypatch.setitem(COLLECTOR_REGISTRY, FAKE_LIBRARY, FakeE2ECollector)
    # _run_collection resolves the session factory inside the function body
    monkeypatch.setattr("app.db.session.async_session_factory", test_session_factory)

    return env


async def _stored_items() -> list[Item]:
    async with test_session_factory() as session:
        result = await session.execute(select(Item))
        return list(result.scalars().all())


class TestTechNewsPipeline:
    async def test_collect_dedup_and_news_api(self, aclient, tech_env):
        headers = bearer(make_e2e_token(tech_env["tenant_id"], str(uuid.uuid4()), role="admin"))

        await scheduler_manager._run_collection(tech_env["source_id"])
        items = await _stored_items()
        # 4 raw entries, the duplicate is dropped by the redis dedup stage
        assert len(items) == 3

        # Second collection round: everything is already known -> nothing new stored
        await scheduler_manager._run_collection(tech_env["source_id"])
        items = await _stored_items()
        assert len(items) == 3

        gpt_item = next(i for i in items if "GPT-5" in i.title)
        assert {"tech", "ai", "llm"} <= set(gpt_item.topic_tags or [])

        resp = await aclient.get("/api/v1/tech/news", headers=headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["meta"]["total"] == 3

        titles = {row["title"] for row in body["data"]}
        assert "OpenAI ships GPT-5 with major LLM reasoning gains" in titles
        assert "SpaceX launches another Starlink batch" in titles
        assert "Atlas headlines the humanoid robot showcase" in titles

        gpt_row = next(row for row in body["data"] if "GPT-5" in row["title"])
        assert {"tech", "ai", "llm"} <= set(gpt_row["topic_tags"])
        # keyword mapping resolves a level-1 domain for the frontend tabs
        assert gpt_row["domain_tag"] == "ai"
        assert gpt_row["source_name"] == "E2E Fake Feed"
        assert gpt_row["hot_score"] > 0

    @pytest.mark.skipif(
        not TEST_DATABASE_URL.startswith("postgresql"),
        reason="topic aggregation uses jsonb_array_elements_text (PostgreSQL only)",
    )
    async def test_topics_stats_aggregate_collected_items(self, aclient, tech_env):
        headers = bearer(make_e2e_token(tech_env["tenant_id"], str(uuid.uuid4())))

        await scheduler_manager._run_collection(tech_env["source_id"])

        resp = await aclient.get("/api/v1/tech/topics", headers=headers)
        assert resp.status_code == 200
        topics = {t["tag"]: t for t in resp.json()["data"]}
        assert topics["tech"]["count"] == 3
        assert topics["ai"]["count"] == 1
        assert topics["robotics"]["count"] == 1
        assert topics["space"]["count"] == 1
        assert topics["ai"]["label"] == "人工智能"
