from sqlalchemy import Boolean, CheckConstraint, Column, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Category(BaseModel):
    __tablename__ = "categories"

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name = Column(String(50), nullable=False)
    slug = Column(String(50), nullable=False)
    description = Column(String(200), nullable=True)
    icon = Column(String(50), default="folder", server_default="folder")
    color = Column(String(7), default="#3B82F6", server_default=text("'#3B82F6'"))
    type = Column(String(20), nullable=False)
    refresh_interval_seconds = Column(Integer, nullable=False, default=300, server_default="300")
    keywords_filter = Column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    priority_sort = Column(Boolean, nullable=False, default=False, server_default="false")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_categories_tenant_slug"),
        CheckConstraint(
            "type IN ('finance', 'tech', 'news', 'custom')",
            name="chk_categories_type",
        ),
        Index("idx_categories_tenant", "tenant_id"),
        Index("idx_categories_type", "tenant_id", "type"),
    )

    tenant = relationship("Tenant", back_populates="categories", lazy="selectin")
    sources = relationship("Source", back_populates="category", cascade="all, delete-orphan", lazy="noload")
    items = relationship("Item", back_populates="category", cascade="all, delete-orphan", lazy="noload")
