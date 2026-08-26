"""Tests for /api/v1/tech endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.api.v1.tech import _get_tech_service
from app.core.security import create_access_token

NOW = datetime.now(UTC).isoformat()


def _token(role="admin", tenant_id=None):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id or str(uuid.uuid4()),
            "role": role,
            "provider": "github",
            "type": "access",
        }
    )


def _headers(tid=None):
    return {"Authorization": f"Bearer {_token(tenant_id=tid)}"}


@pytest.fixture
def mock_tech_svc(app_with_overrides):
    app, _ = app_with_overrides
    mock_svc = AsyncMock()

    async def override():
        return mock_svc

    app.dependency_overrides[_get_tech_service] = override
    yield mock_svc
    app.dependency_overrides.pop(_get_tech_service, None)


def _news_item(**overrides):
    base = {
        "id": str(uuid.uuid4()),
        "title": "Test News Article",
        "summary": "A short summary",
        "url": "https://example.com/article",
        "source_name": "TechCrunch",
        "source_id": str(uuid.uuid4()),
        "category_id": str(uuid.uuid4()),
        "topic_tags": ["ai", "ml"],
        "domain_tag": "ai",
        "published_at": NOW,
        "fetched_at": NOW,
        "image_url": None,
        "priority": 3,
        "extra_data": None,
        "hot_score": 95.5,
    }
    base.update(overrides)
    return base


class TestTechNews:
    def test_news_default_params(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [_news_item()],
            "meta": {"total": 1, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/tech/news", headers=_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["meta"]["total"] == 1
        assert data["data"][0]["title"] == "Test News Article"

    def test_news_with_domain_sort(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

        resp = client.get(
            "/api/v1/tech/news?domain=ai&sort=time&page=2&page_size=10",
            headers=_headers(),
        )
        assert resp.status_code == 200

    def test_news_with_subcategory_source_since(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

        resp = client.get(
            "/api/v1/tech/news?subcategory=ml&source_id=abc&since=2024-01-01&sort=relevance",
            headers=_headers(),
        )
        assert resp.status_code == 200

    def test_news_invalid_sort(self, client, mock_tech_svc):
        resp = client.get("/api/v1/tech/news?sort=invalid", headers=_headers())
        assert resp.status_code == 422

    def test_news_with_tag(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/tech/news?tag=llm", headers=_headers())
        assert resp.status_code == 200
        assert mock_tech_svc.get_news.await_args.kwargs["tag"] == "llm"

    def test_news_tag_stacks_with_domain(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/tech/news?domain=ai&tag=llm", headers=_headers())
        assert resp.status_code == 200
        kwargs = mock_tech_svc.get_news.await_args.kwargs
        assert kwargs["domain"] == "ai"
        assert kwargs["tag"] == "llm"

    def test_news_empty_tag_forwarded_for_service_to_ignore(self, client, mock_tech_svc):
        """Empty tag values reach the service (which ignores them) instead of
        failing validation — same tolerant style as the other tag filters."""
        mock_tech_svc.get_news.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/tech/news?tag=", headers=_headers())
        assert resp.status_code == 200
        assert mock_tech_svc.get_news.await_args.kwargs["tag"] == ""

    def test_news_without_tag_forwards_none(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [],
            "meta": {"total": 0, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/tech/news", headers=_headers())
        assert resp.status_code == 200
        assert mock_tech_svc.get_news.await_args.kwargs["tag"] is None

    def test_news_string_timestamps(self, client, mock_tech_svc):
        mock_tech_svc.get_news.return_value = {
            "data": [
                _news_item(
                    published_at="2024-01-15T10:30:00Z",
                    fetched_at="2024-01-15T11:00:00Z",
                )
            ],
            "meta": {"total": 1, "page": 1, "page_size": 20},
        }

        resp = client.get("/api/v1/tech/news", headers=_headers())
        assert resp.status_code == 200

    def test_news_no_auth(self, client, mock_tech_svc):
        resp = client.get("/api/v1/tech/news")
        assert resp.status_code == 401


class TestTechTopics:
    def test_topics_default(self, client, mock_tech_svc):
        mock_tech_svc.get_topics.return_value = [
            {"tag": "ai", "label": "AI/ML", "count": 150, "last_active_at": NOW},
            {"tag": "robotics", "label": "Robotics", "count": 80, "last_active_at": NOW},
        ]

        resp = client.get("/api/v1/tech/topics", headers=_headers())
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 2

    def test_topics_with_domain(self, client, mock_tech_svc):
        mock_tech_svc.get_topics.return_value = [
            {"tag": "llm", "label": "LLM", "count": 50, "last_active_at": "2024-01-15T10:00:00Z"},
        ]

        resp = client.get("/api/v1/tech/topics?domain=ai", headers=_headers())
        assert resp.status_code == 200

    def test_topics_no_auth(self, client, mock_tech_svc):
        resp = client.get("/api/v1/tech/topics")
        assert resp.status_code == 401

    def test_topics_invalid_last_active(self, client, mock_tech_svc):
        mock_tech_svc.get_topics.return_value = [
            {"tag": "rust", "label": "Rust", "count": 30, "last_active_at": "bad-date"},
        ]

        resp = client.get("/api/v1/tech/topics", headers=_headers())
        assert resp.status_code == 200
