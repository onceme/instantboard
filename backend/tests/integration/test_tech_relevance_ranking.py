"""Integration tests for the personalized relevance re-rank (design tech-tab.md
§3.5.1): candidate pool = priority DESC limited to min(page_size*3, 300), Python
re-score priority + tag-overlap*2 (stable), page sliced from the sorted pool.

Service-level cases run against the real sqlite engine via TechService
(db_session rolls everything back); the endpoint case verifies that
GET /tech/news loads the caller's preferences and hands them to the service.
"""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from sqlalchemy.sql import sqltypes

from app.api.v1.tech import _get_tech_service
from app.core.security import create_access_token
from app.models.category import Category
from app.models.item import Item
from app.models.source import Source
from app.models.tenant import Tenant
from app.models.user import User
from app.services.tech import TechService
from tests.conftest import test_session_factory
from tests.integration.conftest import make_auth_header

# Same lenient str-UUID binding guard as test_api_sorting.py: services bind the
# JWT's str tenant id against UUID columns (asyncpg accepts str; sqlite's Uuid
# bind processor would reject it without this).
if not getattr(sqltypes.Uuid.bind_processor, "_ib_accepts_str", False):
    _original_uuid_bind_processor = sqltypes.Uuid.bind_processor

    def _uuid_bind_processor_accepting_str(self, dialect):
        process = _original_uuid_bind_processor(self, dialect)
        if process is None:
            return None

        def process_lenient(value):
            if value is not None and not isinstance(value, uuid.UUID):
                value = uuid.UUID(str(value))
            return process(value)

        return process_lenient

    _uuid_bind_processor_accepting_str._ib_accepts_str = True  # type: ignore[attr-defined]
    sqltypes.Uuid.bind_processor = _uuid_bind_processor_accepting_str


async def _seed_ranking_fixture(db_session) -> dict:
    """Tenant + category + source + three items with priorities diverging from
    favorite-tag overlap."""
    now = datetime.now(UTC)
    tenant = Tenant(name="Rank Tenant", slug=f"rank-{uuid.uuid4().hex[:12]}", plan="free")
    db_session.add(tenant)
    await db_session.flush()

    category = Category(
        tenant_id=tenant.id,
        name="Rank Feed",
        slug=f"rank-feed-{uuid.uuid4().hex[:8]}",
        type="custom",
        refresh_interval_seconds=300,
        is_active=True,
    )
    db_session.add(category)
    await db_session.flush()

    source = Source(
        tenant_id=tenant.id,
        category_id=category.id,
        name="Rank Src",
        source_type="rss",
        url="https://example.com/feed",
        config={},
        refresh_interval_seconds=300,
        is_active=True,
        priority=3,
    )
    db_session.add(source)
    await db_session.flush()

    items = {}
    for key, priority, tags, hours in [
        # Highest priority, no favorite overlap
        ("high", 8, ["tech"], 1),
        ("mid", 7, ["tech", "robotics"], 2),
        # Lowest priority, overlaps both favorite tags: 5 + 2*2 = 9 beats "high"
        ("low", 5, ["tech", "llm", "ai"], 3),
    ]:
        item = Item(
            tenant_id=tenant.id,
            category_id=category.id,
            source_id=source.id,
            title=f"Rank {key}",
            url=f"https://example.com/{key}-{uuid.uuid4().hex}",
            topic_tags=tags,
            priority=priority,
            published_at=now - timedelta(hours=hours),
            fetched_at=now,
        )
        db_session.add(item)
        items[key] = item
    await db_session.flush()

    return {"tenant_id": str(tenant.id), "category_id": category.id, "items": items}


def _titles(result: dict) -> list[str]:
    return [row["title"] for row in result["data"]]


class TestListCategoryItemsRelevance:
    async def test_no_preferences_keeps_pure_priority_order(self, db_session):
        fixture = await _seed_ranking_fixture(db_session)
        service = TechService(db_session, AsyncMock())

        result = await service.list_category_items(
            category_id=fixture["category_id"],
            tenant_id=fixture["tenant_id"],
            sort="relevance",
            page_size=10,
        )

        assert _titles(result) == ["Rank high", "Rank mid", "Rank low"]
        assert result["meta"]["total"] == 3

    async def test_empty_favorite_tags_degrade_to_pure_priority(self, db_session):
        fixture = await _seed_ranking_fixture(db_session)
        service = TechService(db_session, AsyncMock())

        result = await service.list_category_items(
            category_id=fixture["category_id"],
            tenant_id=fixture["tenant_id"],
            sort="relevance",
            page_size=10,
            user_preferences={"favorite_tags": []},
        )

        assert _titles(result) == ["Rank high", "Rank mid", "Rank low"]

    async def test_favorite_overlap_moves_items_first(self, db_session):
        fixture = await _seed_ranking_fixture(db_session)
        service = TechService(db_session, AsyncMock())

        result = await service.list_category_items(
            category_id=fixture["category_id"],
            tenant_id=fixture["tenant_id"],
            sort="relevance",
            page_size=10,
            user_preferences={"favorite_tags": ["llm", "ai"]},
        )

        # low: 5 + 2 overlaps * 2 = 9, high: 8, mid: 7
        assert _titles(result) == ["Rank low", "Rank high", "Rank mid"]
        assert result["meta"]["total"] == 3

    async def test_reranked_pool_is_sliced_for_pagination(self, db_session):
        fixture = await _seed_ranking_fixture(db_session)
        service = TechService(db_session, AsyncMock())
        prefs = {"favorite_tags": ["llm", "ai"]}

        page1 = await service.list_category_items(
            category_id=fixture["category_id"],
            tenant_id=fixture["tenant_id"],
            sort="relevance",
            page=1,
            page_size=2,
            user_preferences=prefs,
        )
        page2 = await service.list_category_items(
            category_id=fixture["category_id"],
            tenant_id=fixture["tenant_id"],
            sort="relevance",
            page=2,
            page_size=2,
            user_preferences=prefs,
        )

        assert _titles(page1) == ["Rank low", "Rank high"]
        assert _titles(page2) == ["Rank mid"]
        assert page1["meta"]["total"] == 3
        assert page2["meta"]["page"] == 2

    async def test_other_sorts_ignore_preferences(self, db_session):
        fixture = await _seed_ranking_fixture(db_session)
        service = TechService(db_session, AsyncMock())

        result = await service.list_category_items(
            category_id=fixture["category_id"],
            tenant_id=fixture["tenant_id"],
            sort="hot",
            page_size=10,
            user_preferences={"favorite_tags": ["llm", "ai"]},
        )

        # hot re-sorts the page by hot_score (priority * freshness decay), never by tags
        assert set(_titles(result)) == {"Rank high", "Rank mid", "Rank low"}
        assert result["data"][0]["title"] == "Rank high"


class TestCandidatePoolCap:
    async def test_low_priority_match_outside_pool_never_surfaces(self, db_session):
        """page_size=3 -> pool = 9 rows; the priority-1 item (which would rank
        mid-pack once boosted) sits 10th by priority and stays unreachable."""
        now = datetime.now(UTC)
        tenant = Tenant(name="Cap Tenant", slug=f"cap-{uuid.uuid4().hex[:12]}", plan="free")
        db_session.add(tenant)
        await db_session.flush()

        category = Category(
            tenant_id=tenant.id,
            name="Cap Feed",
            slug=f"cap-feed-{uuid.uuid4().hex[:8]}",
            type="custom",
            refresh_interval_seconds=300,
            is_active=True,
        )
        db_session.add(category)
        await db_session.flush()

        source = Source(
            tenant_id=tenant.id,
            category_id=category.id,
            name="Cap Src",
            source_type="rss",
            url="https://example.com/cap-feed",
            config={},
            refresh_interval_seconds=300,
            is_active=True,
            priority=3,
        )
        db_session.add(source)
        await db_session.flush()

        # p10..p2 carry no favorite tags; p1 matches both favorites but is last by priority
        for priority in range(10, 1, -1):
            db_session.add(
                Item(
                    tenant_id=tenant.id,
                    category_id=category.id,
                    source_id=source.id,
                    title=f"Cap p{priority}",
                    url=f"https://example.com/cap-{priority}-{uuid.uuid4().hex}",
                    topic_tags=["tech"],
                    priority=priority,
                    published_at=now - timedelta(hours=priority),
                    fetched_at=now,
                )
            )
        db_session.add(
            Item(
                tenant_id=tenant.id,
                category_id=category.id,
                source_id=source.id,
                title="Cap p1",
                url=f"https://example.com/cap-1-{uuid.uuid4().hex}",
                topic_tags=["tech", "llm", "ai"],
                priority=1,
                published_at=now - timedelta(hours=1),
                fetched_at=now,
            )
        )
        await db_session.flush()

        service = TechService(db_session, AsyncMock())
        prefs = {"favorite_tags": ["llm", "ai"]}

        collected: list[str] = []
        for page in (1, 2, 3, 4):
            result = await service.list_category_items(
                category_id=category.id,
                tenant_id=str(tenant.id),
                sort="relevance",
                page=page,
                page_size=3,
                user_preferences=prefs,
            )
            collected.extend(_titles(result))
            # The filtered total always reflects every row, pool cap aside
            assert result["meta"]["total"] == 10

        # Pool holds exactly the 9 highest-priority rows
        assert collected == [f"Cap p{p}" for p in range(10, 1, -1)]
        assert "Cap p1" not in collected


class TestTechNewsPreferencesWiring:
    """GET /tech/news must load the caller's users.preferences and forward them to
    TechService.get_news — but only for sort=relevance."""

    async def _seed_user_with_prefs(self) -> dict:
        tenant_id = uuid.uuid4()
        async with test_session_factory() as session:
            session.add(Tenant(id=tenant_id, name="Wire Tenant", slug=f"wire-{tenant_id.hex[:12]}", plan="free"))
            await session.flush()

            user = User(
                tenant_id=tenant_id,
                email=f"wire-{tenant_id.hex[:10]}@example.com",
                name="Wire User",
                sso_provider="github",
                sso_provider_id=f"gh-{tenant_id.hex}",
                role="member",
                preferences={"favorite_tags": ["llm", "drone"]},
            )
            session.add(user)
            await session.commit()

        token = create_access_token(
            {
                "sub": str(user.id),
                "tenant_id": str(tenant_id),
                "role": "member",
                "provider": "github",
                "type": "access",
            }
        )
        return {"headers": {"Authorization": f"Bearer {token}"}, "user_id": str(user.id), "tenant_id": str(tenant_id)}

    def _install_mock_service(self, app):
        mock_svc = AsyncMock()
        mock_svc.get_news.return_value = {"data": [], "meta": {"total": 0, "page": 1, "page_size": 20}}

        async def override():
            return mock_svc

        app.dependency_overrides[_get_tech_service] = override
        return mock_svc

    async def test_relevance_forwards_user_preferences(self, client, app_with_overrides):
        app, _ = app_with_overrides
        mock_svc = self._install_mock_service(app)

        fixture = await self._seed_user_with_prefs()
        resp = client.get("/api/v1/tech/news?sort=relevance", headers=fixture["headers"])
        assert resp.status_code == 200
        assert mock_svc.get_news.await_args.kwargs["user_preferences"] == {"favorite_tags": ["llm", "drone"]}
        app.dependency_overrides.pop(_get_tech_service, None)

    async def test_hot_sort_skips_preferences_lookup(self, client, app_with_overrides):
        app, _ = app_with_overrides
        mock_svc = self._install_mock_service(app)

        fixture = await self._seed_user_with_prefs()
        resp = client.get("/api/v1/tech/news?sort=hot", headers=fixture["headers"])
        assert resp.status_code == 200
        assert mock_svc.get_news.await_args.kwargs["user_preferences"] is None
        app.dependency_overrides.pop(_get_tech_service, None)

    async def test_relevance_with_unknown_user_passes_none(self, client, app_with_overrides):
        app, _ = app_with_overrides
        mock_svc = self._install_mock_service(app)

        headers, _, _ = make_auth_header(role="member")
        resp = client.get("/api/v1/tech/news?sort=relevance", headers=headers)
        assert resp.status_code == 200
        assert mock_svc.get_news.await_args.kwargs["user_preferences"] is None
        app.dependency_overrides.pop(_get_tech_service, None)
