"""Integration tests for tenant-level settings overrides (P2-16).

Endpoints under test: GET/PUT /api/v1/tenant/settings, plus the consumer
side (category color overrides merged into GET /categories responses).
Runs against the real test database; every test provisions its own tenant
and categories via the public API, so no fixed fixtures are required.
"""

import uuid

from app.core.security import create_access_token


def _token(role, tenant_id):
    return create_access_token(
        {
            "sub": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "role": role,
            "provider": "github",
            "type": "access",
        }
    )


def _headers(role, tenant_id):
    return {"Authorization": f"Bearer {_token(role, tenant_id)}"}


def _make_admin_headers():
    # Admin-panel session on a throwaway tenant: role=admin is all the admin
    # CRUD endpoints check.
    return _headers("admin", str(uuid.uuid4()))


def _create_tenant(client, settings=None):
    body = {
        "name": f"Tenant {uuid.uuid4().hex[:6]}",
        "slug": f"t-{uuid.uuid4().hex[:10]}",
        "plan": "free",
    }
    if settings is not None:
        body["settings"] = settings
    resp = client.post("/api/v1/admin/tenants", headers=_make_admin_headers(), json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["id"]


def _create_category(client, tenant_id, color="#123456"):
    slug = f"cat-{uuid.uuid4().hex[:8]}"
    resp = client.post(
        "/api/v1/categories",
        headers=_headers("admin", tenant_id),
        json={
            "name": f"Category {slug}",
            "slug": slug,
            "type": "custom",
            "color": color,
            "refresh_interval_seconds": 120,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


class TestGetTenantSettings:
    def test_defaults_to_empty_overrides(self, client):
        tenant_id = _create_tenant(client)
        resp = client.get("/api/v1/tenant/settings", headers=_headers("member", tenant_id))
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["data"] == {"refresh_overrides": {}, "color_overrides": {}}

    def test_no_auth_is_401(self, client):
        resp = client.get("/api/v1/tenant/settings")
        assert resp.status_code == 401

    def test_readable_by_member(self, client):
        tenant_id = _create_tenant(client)
        resp = client.get("/api/v1/tenant/settings", headers=_headers("viewer", tenant_id))
        assert resp.status_code == 200


class TestPutTenantSettings:
    def test_roundtrip_and_other_keys_preserved(self, client):
        tenant_id = _create_tenant(client, settings={"color_scheme": "international"})
        category = _create_category(client, tenant_id)
        slug = category["slug"]

        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"refresh_overrides": {slug: 60}, "color_overrides": {slug: "#FF0000"}},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["refresh_overrides"] == {slug: 60}
        assert data["color_overrides"] == {slug: "#FF0000"}

        resp = client.get("/api/v1/tenant/settings", headers=_headers("member", tenant_id))
        assert resp.json()["data"] == {
            "refresh_overrides": {slug: 60},
            "color_overrides": {slug: "#FF0000"},
        }

        # The raw row keeps unrelated settings keys next to the overrides.
        admin_resp = client.get(f"/api/v1/admin/tenants/{tenant_id}", headers=_make_admin_headers())
        stored = admin_resp.json()["data"]["settings"]
        assert stored["color_scheme"] == "international"
        assert stored["refresh_overrides"] == {slug: 60}
        assert stored["color_overrides"] == {slug: "#FF0000"}

    def test_omitted_key_is_cleared_whole_replace(self, client):
        tenant_id = _create_tenant(client)
        slug = _create_category(client, tenant_id)["slug"]

        client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"refresh_overrides": {slug: 60}, "color_overrides": {slug: "#FF0000"}},
        )
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"color_overrides": {slug: "#00FF00"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["refresh_overrides"] == {}
        assert data["color_overrides"] == {slug: "#00FF00"}

    def test_member_forbidden(self, client):
        tenant_id = _create_tenant(client)
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("member", tenant_id),
            json={"refresh_overrides": {}},
        )
        assert resp.status_code == 403
        assert resp.json()["detail"]["error"]["code"] == "FORBIDDEN"

    def test_no_auth_is_401(self, client):
        resp = client.put("/api/v1/tenant/settings", json={"refresh_overrides": {}})
        assert resp.status_code == 401

    def test_invalid_refresh_range_400(self, client):
        tenant_id = _create_tenant(client)
        slug = _create_category(client, tenant_id)["slug"]
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"refresh_overrides": {slug: 5}},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == f"refresh_overrides.{slug}"

    def test_invalid_color_format_400(self, client):
        tenant_id = _create_tenant(client)
        slug = _create_category(client, tenant_id)["slug"]
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"color_overrides": {slug: "red"}},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert error["details"][0]["field"] == f"color_overrides.{slug}"

    def test_unknown_slug_400(self, client):
        tenant_id = _create_tenant(client)
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"refresh_overrides": {"ghost-slug": 60}},
        )
        assert resp.status_code == 400
        error = resp.json()["detail"]["error"]
        assert error["code"] == "VALIDATION_ERROR"
        assert "Unknown category slug" in error["details"][0]["message"]


class TestCrossTenantIsolation:
    def test_overrides_are_per_tenant(self, client):
        tenant_a = _create_tenant(client)
        tenant_b = _create_tenant(client)
        slug_a = _create_category(client, tenant_a)["slug"]
        slug_b = _create_category(client, tenant_b)["slug"]

        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_a),
            json={"refresh_overrides": {slug_a: 60}, "color_overrides": {slug_a: "#FF0000"}},
        )
        assert resp.status_code == 200

        # Tenant B sees no overrides from A...
        resp = client.get("/api/v1/tenant/settings", headers=_headers("admin", tenant_b))
        assert resp.json()["data"] == {"refresh_overrides": {}, "color_overrides": {}}

        # ...and cannot reference A's category slugs (unknown in B's scope).
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_b),
            json={"refresh_overrides": {slug_a: 60}},
        )
        assert resp.status_code == 400

        # B's own slugs remain valid.
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_b),
            json={"color_overrides": {slug_b: "#00FF00"}},
        )
        assert resp.status_code == 200

        # A's settings were untouched by B's write.
        resp = client.get("/api/v1/tenant/settings", headers=_headers("admin", tenant_a))
        assert resp.json()["data"] == {
            "refresh_overrides": {slug_a: 60},
            "color_overrides": {slug_a: "#FF0000"},
        }


class TestCategoryColorOverrideConsumption:
    def test_list_and_detail_reflect_override(self, client):
        tenant_id = _create_tenant(client)
        cat_overridden = _create_category(client, tenant_id, color="#123456")
        cat_plain = _create_category(client, tenant_id, color="#654321")

        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"color_overrides": {cat_overridden["slug"]: "#FF0000"}},
        )
        assert resp.status_code == 200

        headers = _headers("member", tenant_id)

        # GET /categories: overridden slug shows the override, others keep stored color.
        resp = client.get("/api/v1/categories", headers=headers, params={"page_size": 100})
        assert resp.status_code == 200
        colors = {c["slug"]: c["color"] for c in resp.json()["data"]}
        assert colors[cat_overridden["slug"]] == "#FF0000"
        assert colors[cat_plain["slug"]] == "#654321"

        # GET /categories/{id} merges the override too.
        resp = client.get(f"/api/v1/categories/{cat_overridden['id']}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["data"]["color"] == "#FF0000"

        # Clearing the override restores the stored color — the override lives
        # in tenant settings only, the category row is never rewritten.
        resp = client.put(
            "/api/v1/tenant/settings",
            headers=_headers("admin", tenant_id),
            json={"color_overrides": {}},
        )
        assert resp.status_code == 200
        resp = client.get(f"/api/v1/categories/{cat_overridden['id']}", headers=headers)
        assert resp.json()["data"]["color"] == "#123456"
