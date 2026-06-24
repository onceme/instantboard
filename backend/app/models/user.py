from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class User(BaseModel):
    __tablename__ = "users"

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    email = Column(String(255), nullable=False)
    name = Column(String(100), nullable=False)
    avatar_url = Column(Text, nullable=True)
    sso_provider = Column(String(20), nullable=False)
    sso_provider_id = Column(String(255), nullable=False)
    role = Column(
        String(20),
        nullable=False,
        default="member",
        server_default="member",
    )
    preferences = Column(JSONB, nullable=False, default=dict, server_default="'{}'")
    last_login_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        UniqueConstraint("sso_provider", "sso_provider_id", name="uq_users_sso"),
        CheckConstraint(
            "sso_provider IN ('google', 'azure_ad', 'github', 'apple', 'facebook')",
            name="chk_users_sso_provider",
        ),
        CheckConstraint(
            "role IN ('admin', 'member', 'viewer')",
            name="chk_users_role",
        ),
        Index("idx_users_tenant", "tenant_id"),
        Index("idx_users_sso", "sso_provider", "sso_provider_id"),
    )

    tenant = relationship("Tenant", back_populates="users")
    watchlist_items = relationship("WatchlistItem", back_populates="user", cascade="all, delete-orphan")
    sse_connections = relationship("SSEConnection", back_populates="user", cascade="all, delete-orphan")
