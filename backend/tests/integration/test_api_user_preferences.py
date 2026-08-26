"""Integration tests for GET/PUT /api/v1/users/me/preferences (favorite_tags used
by the tech relevance re-rank). Rows are seeded through the ORM with committed
transactions (test_api_sorting.py pattern)."""

import uuid

from app.core.security import create_access_token
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.user import MAX_FAVORITE_TAGS
from tests.conftest import test_session_factory


async def _seed_user(preferences: dict | None = None) -> dict:
    """Seed a tenant + user and return auth headers plus ids."""
    tenant_id = uuid.uuid4()
    async with test_session_factory() as session:
        session.add(Tenant(id=tenant_id, name="Prefs Tenant", slug=f"prefs-{tenant_id.hex[:12]}", plan="free"))
        await session.flush()

        user = User(
            tenant_id=tenant_id,
            email=f"prefs-{tenant_id.hex[:10]}@example.com",
            name="Prefs User",
            sso_provider="github",
            sso_provider_id=f"gh-{tenant_id.hex}",
            role="member",
            preferences=preferences if preferences is not None else {},
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


class TestGetPreferences:
    async def test_no_auth_returns_401(self, client):
        resp = client.get("/api/v1/users/me/preferences")
        assert resp.status_code == 401

    async def test_default_preferences_return_empty_tags(self, client):
        fixture = await _seed_user()
        resp = client.get("/api/v1/users/me/preferences", headers=fixture["headers"])
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {"favorite_tags": []}

    async def test_existing_preferences_are_returned(self, client):
        fixture = await _seed_user(preferences={"favorite_tags": ["llm", "drone"]})
        resp = client.get("/api/v1/users/me/preferences", headers=fixture["headers"])
        assert resp.status_code == 200
        assert resp.json()["data"] == {"favorite_tags": ["llm", "drone"]}

    async def test_unknown_user_returns_401(self, client):
        tenant_id = str(uuid.uuid4())
        token = create_access_token(
            {
                "sub": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "role": "member",
                "provider": "github",
                "type": "access",
            }
        )
        resp = client.get("/api/v1/users/me/preferences", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401
        assert resp.json()["detail"]["error"]["code"] == "INVALID_TOKEN"


class TestUpdatePreferences:
    async def test_no_auth_returns_401(self, client):
        resp = client.put("/api/v1/users/me/preferences", json={"favorite_tags": ["llm"]})
        assert resp.status_code == 401

    async def test_put_echoes_normalized_tags_and_persists(self, client):
        fixture = await _seed_user()

        put = client.put(
            "/api/v1/users/me/preferences",
            headers=fixture["headers"],
            json={"favorite_tags": ["LLM", "llm", "Drone"]},
        )
        assert put.status_code == 200
        assert put.json()["data"] == {"favorite_tags": ["llm", "drone"]}

        get = client.get("/api/v1/users/me/preferences", headers=fixture["headers"])
        assert get.status_code == 200
        assert get.json()["data"] == {"favorite_tags": ["llm", "drone"]}

    async def test_put_replaces_previous_tags_but_keeps_other_keys(self, client):
        fixture = await _seed_user(preferences={"theme": "dark", "favorite_tags": ["old"]})

        put = client.put(
            "/api/v1/users/me/preferences",
            headers=fixture["headers"],
            json={"favorite_tags": ["llm"]},
        )
        assert put.status_code == 200
        assert put.json()["data"] == {"favorite_tags": ["llm"]}

    async def test_empty_list_clears_tags(self, client):
        fixture = await _seed_user(preferences={"favorite_tags": ["llm"]})

        put = client.put("/api/v1/users/me/preferences", headers=fixture["headers"], json={"favorite_tags": []})
        assert put.status_code == 200
        assert put.json()["data"] == {"favorite_tags": []}

        get = client.get("/api/v1/users/me/preferences", headers=fixture["headers"])
        assert get.json()["data"] == {"favorite_tags": []}

    async def test_invalid_tag_returns_400_validation_error(self, client):
        fixture = await _seed_user()

        resp = client.put(
            "/api/v1/users/me/preferences",
            headers=fixture["headers"],
            json={"favorite_tags": ["llm", "bad tag"]},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == "favorite_tags"

        # Rejected write must not persist
        get = client.get("/api/v1/users/me/preferences", headers=fixture["headers"])
        assert get.json()["data"] == {"favorite_tags": []}

    async def test_invalid_tag_is_not_lowercased_into_validity(self, client):
        fixture = await _seed_user()
        resp = client.put(
            "/api/v1/users/me/preferences",
            headers=fixture["headers"],
            json={"favorite_tags": ["BAD_TAG"]},
        )
        assert resp.status_code == 400

    async def test_too_many_tags_returns_400(self, client):
        fixture = await _seed_user()
        resp = client.put(
            "/api/v1/users/me/preferences",
            headers=fixture["headers"],
            json={"favorite_tags": [f"tag-{i}" for i in range(MAX_FAVORITE_TAGS + 1)]},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"]["error"]["code"] == "VALIDATION_ERROR"

    async def test_exactly_max_tags_is_accepted(self, client):
        fixture = await _seed_user()
        tags = [f"tag-{i}" for i in range(MAX_FAVORITE_TAGS)]
        resp = client.put(
            "/api/v1/users/me/preferences",
            headers=fixture["headers"],
            json={"favorite_tags": tags},
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["favorite_tags"] == tags

    async def test_missing_body_returns_422(self, client):
        fixture = await _seed_user()
        resp = client.put("/api/v1/users/me/preferences", headers=fixture["headers"], json={})
        assert resp.status_code == 422
