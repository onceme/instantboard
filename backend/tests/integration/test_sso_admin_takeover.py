"""Real-DB tests for the SSO email-match fallback in AuthService._get_or_create_user.

Security regression tests: the email fallback used to match ANY user row with the same
email, which let an SSO login take over the local admin record (sso_provider='local',
system tenant, role='admin') and accounts of other tenants. Post-fix semantics:

- provider='local' records are never matched (admin identities are isolated per
  docs/dev-guide/design/admin-login.md; SSO with the admin email provisions a fresh member user);
- matching is scoped to the default tenant (where new SSO users are provisioned);
- the main path (same provider + provider_id) still refreshes the profile as before.

Everything runs inside the db_session fixture's transaction and is rolled back, so no
state persists.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select

from app.core.constants import LOCAL_SSO_PROVIDER, SYSTEM_TENANT_ID
from app.models.tenant import Tenant
from app.models.user import User
from app.services.auth import AuthService


async def _ensure_system_tenant(session) -> Tenant:
    # Earlier integration tests may have committed the system tenant; reuse it instead
    # of violating tenants_pkey.
    tenant = await session.get(Tenant, SYSTEM_TENANT_ID)
    if tenant is None:
        tenant = Tenant(name="System", slug=f"sys-{uuid.uuid4().hex[:8]}")
        tenant.id = SYSTEM_TENANT_ID
        session.add(tenant)
        await session.flush()
    return tenant


def _make_sso_info(provider_id: str, email: str | None, name: str = "SSO User") -> SimpleNamespace:
    return SimpleNamespace(provider_id=provider_id, email=email, name=name, avatar_url=None)


class TestSSOEmailTakeoverFix:
    async def test_sso_with_local_admin_email_provisions_member_admin_untouched(self, db_session):
        """Core regression: SSO login with the local admin's email must NOT take over the
        admin record. A fresh member user is provisioned in the default tenant instead,
        while the admin record keeps its provider, role and tenant."""
        email = f"admin-{uuid.uuid4().hex[:10]}@example.com"
        system_tenant = await _ensure_system_tenant(db_session)

        admin = User(
            tenant_id=system_tenant.id,
            email=email,
            name="Local Admin",
            sso_provider=LOCAL_SSO_PROVIDER,
            sso_provider_id=f"{LOCAL_SSO_PROVIDER}:{email}",
            role="admin",
        )
        db_session.add(admin)
        await db_session.flush()
        admin_id = admin.id

        service = AuthService(db_session, AsyncMock())
        user = await service._get_or_create_user("github", _make_sso_info("gh-12345", email))

        # A brand-new member record, distinct from the admin record.
        assert user.id != admin_id
        assert user.role == "member"
        assert user.sso_provider == "github"
        assert user.sso_provider_id == "gh-12345"
        assert user.email == email
        assert user.tenant_id != SYSTEM_TENANT_ID

        # New SSO users belong to the default tenant.
        default_tenant = await service._get_default_tenant()
        assert user.tenant_id == default_tenant.id

        # The admin record is untouched: provider not rewritten, role unchanged.
        await db_session.refresh(admin)
        assert admin.sso_provider == LOCAL_SSO_PROVIDER
        assert admin.sso_provider_id == f"{LOCAL_SSO_PROVIDER}:{email}"
        assert admin.role == "admin"
        assert admin.tenant_id == SYSTEM_TENANT_ID

        # Isolation, not merge: two independent records share the email.
        rows = (await db_session.execute(select(User).where(User.email == email))).scalars().all()
        assert len(rows) == 2

    async def test_existing_user_main_path_still_updates_profile(self, db_session):
        """Main path regression: an existing user with the same (provider, provider_id)
        logs in again and gets their profile refreshed — no duplicate, no re-provisioning."""
        service = AuthService(db_session, AsyncMock())
        default_tenant = await service._get_default_tenant()

        provider_id = f"gh-{uuid.uuid4().hex[:10]}"
        user = User(
            tenant_id=default_tenant.id,
            email=f"old-{uuid.uuid4().hex[:10]}@example.com",
            name="Old Name",
            sso_provider="github",
            sso_provider_id=provider_id,
            role="member",
        )
        db_session.add(user)
        await db_session.flush()

        new_email = f"new-{uuid.uuid4().hex[:10]}@example.com"
        result = await service._get_or_create_user(
            "github",
            SimpleNamespace(
                provider_id=provider_id,
                email=new_email,
                name="New Name",
                avatar_url="https://av.example.com/a.png",
            ),
        )

        assert result.id == user.id
        assert result.email == new_email
        assert result.name == "New Name"
        assert result.avatar_url == "https://av.example.com/a.png"
        assert result.role == "member"
        assert result.tenant_id == default_tenant.id

        rows = (await db_session.execute(select(User).where(User.sso_provider_id == provider_id))).scalars().all()
        assert len(rows) == 1

    async def test_email_fallback_does_not_match_other_tenants(self, db_session):
        """Cross-tenant regression: the email fallback must never match records outside
        the default tenant — not even role='admin' rows of other tenants."""
        email = f"cross-{uuid.uuid4().hex[:10]}@example.com"
        other_tenant = Tenant(name="Other Tenant", slug=f"other-{uuid.uuid4().hex[:8]}", plan="free", settings={})
        db_session.add(other_tenant)
        await db_session.flush()

        foreign_user = User(
            tenant_id=other_tenant.id,
            email=email,
            name="Foreign Admin",
            sso_provider="google",
            sso_provider_id=f"g-{uuid.uuid4().hex[:10]}",
            role="admin",
        )
        db_session.add(foreign_user)
        await db_session.flush()

        service = AuthService(db_session, AsyncMock())
        user = await service._get_or_create_user("github", _make_sso_info(f"gh-{uuid.uuid4().hex[:10]}", email))

        # A fresh default-tenant member instead of the foreign admin record.
        assert user.id != foreign_user.id
        assert user.tenant_id != other_tenant.id
        assert user.role == "member"
        default_tenant = await service._get_default_tenant()
        assert user.tenant_id == default_tenant.id

        await db_session.refresh(foreign_user)
        assert foreign_user.sso_provider == "google"
        assert foreign_user.role == "admin"

    async def test_email_fallback_still_binds_same_tenant_non_local_record(self, db_session):
        """Original intent preserved: within the default tenant, a non-local record with
        the same email is reused (SSO identity re-bind / profile backfill), not duplicated."""
        service = AuthService(db_session, AsyncMock())
        default_tenant = await service._get_default_tenant()

        email = f"link-{uuid.uuid4().hex[:10]}@example.com"
        existing = User(
            tenant_id=default_tenant.id,
            email=email,
            name="Existing",
            sso_provider="google",
            sso_provider_id=f"g-{uuid.uuid4().hex[:10]}",
            role="member",
        )
        db_session.add(existing)
        await db_session.flush()

        result = await service._get_or_create_user("github", _make_sso_info(f"gh-{uuid.uuid4().hex[:10]}", email))

        assert result.id == existing.id
        assert result.sso_provider == "github"
        assert result.name == "SSO User"

        rows = (await db_session.execute(select(User).where(User.email == email))).scalars().all()
        assert len(rows) == 1
