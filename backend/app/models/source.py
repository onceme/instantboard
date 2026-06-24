import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.base import Base, BaseModel


class Source(BaseModel):
    __tablename__ = "sources"

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
    name = Column(String(100), nullable=False)
    source_type = Column(String(20), nullable=False)
    url = Column(Text, nullable=False)
    config = Column(JSONB, nullable=False, default=dict, server_default="'{}'")
    refresh_interval_seconds = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    priority = Column(Integer, nullable=False, default=5, server_default="5")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('rss', 'api', 'web_scrape', 'social')",
            name="chk_sources_type",
        ),
        Index("idx_sources_category", "category_id"),
        Index("idx_sources_tenant", "tenant_id"),
        Index("idx_sources_type", "tenant_id", "source_type"),
    )

    tenant = relationship("Tenant", back_populates="sources")
    category = relationship("Category", back_populates="sources")
    health = relationship("SourceHealth", back_populates="source", uselist=False, cascade="all, delete-orphan")
    items = relationship("Item", back_populates="source", cascade="all, delete-orphan")


class SourceHealth(Base):
    __tablename__ = "source_health"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status = Column(
        String(10),
        nullable=False,
        default="healthy",
        server_default="healthy",
    )
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    consecutive_failures = Column(Integer, nullable=False, default=0, server_default="0")
    total_fetches_24h = Column(Integer, nullable=False, default=0, server_default="0")
    success_count_24h = Column(Integer, nullable=False, default=0, server_default="0")
    avg_response_time_ms = Column(Integer, nullable=False, default=0, server_default="0")
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
        onupdate=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy', 'degraded', 'down')",
            name="chk_source_health_status",
        ),
        Index("idx_source_health_status", "status"),
    )

    source = relationship("Source", back_populates="health")
