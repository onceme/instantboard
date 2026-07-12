import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.base import Base


class Item(Base):
    __tablename__ = "items"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    category_id = Column(
        UUID(as_uuid=True),
        ForeignKey("categories.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    title = Column(String(500), nullable=False)
    summary = Column(Text, nullable=True)
    url = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=False)
    fetched_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    topic_tags = Column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    extra_data = Column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    priority = Column(Integer, nullable=False, default=5, server_default="5")
    is_processed = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "url",
            "published_at",
            name="uq_items_dedup",
        ),
        Index("idx_items_category", "category_id", "published_at"),
        Index("idx_items_tenant_time", "tenant_id", "published_at"),
        Index("idx_items_tags", "topic_tags", postgresql_using="gin"),
        Index("idx_items_extra_data", "extra_data", postgresql_using="gin"),
        Index("idx_items_source", "source_id"),
    )

    tenant = relationship("Tenant", lazy="selectin")
    category = relationship("Category", back_populates="items", lazy="selectin")
    source = relationship("Source", back_populates="items", lazy="selectin")
