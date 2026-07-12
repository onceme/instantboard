#!/usr/bin/env python3
"""
Seed script to create initial admin user for testing.
Run after database is initialized: docker exec docker-api-1 python seed_data.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app")

from sqlalchemy import select

from app.db.session import async_session_factory
from app.models import Tenant, User

SEED_ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@instantboard.dev")
SEED_ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "admin123")  # noqa: F841
SEED_ADMIN_NAME = os.getenv("SEED_ADMIN_NAME", "Admin User")
SEED_ADMIN_PROVIDER = os.getenv("SEED_ADMIN_PROVIDER", "github")


async def create_admin_user():
    """Create admin user if not exists."""
    async with async_session_factory() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.slug == "default")
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            print("ERROR: Default tenant not found. Run migrations first.")
            return False

        result = await session.execute(
            select(User).where(
                User.email == SEED_ADMIN_EMAIL,
                User.tenant_id == tenant.id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Admin user already exists: {existing.email}")
            return True

        admin = User(
            tenant_id=tenant.id,
            email=SEED_ADMIN_EMAIL,
            name=SEED_ADMIN_NAME,
            role="admin",
            sso_provider=SEED_ADMIN_PROVIDER,
            sso_provider_id=f"seed_{SEED_ADMIN_EMAIL}",
        )

        session.add(admin)
        await session.commit()

        print(f"Created admin user:")
        print(f"   Email: {admin.email}")
        print(f"   Name: {admin.name}")
        print(f"   Role: {admin.role}")
        print(f"   Provider: {admin.sso_provider}")
        print(f"   Tenant: {tenant.name}")
        return True


if __name__ == "__main__":
    success = asyncio.run(create_admin_user())
    sys.exit(0 if success else 1)
