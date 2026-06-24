import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base import Base


class DashboardSnapshot(Base):
    __tablename__ = "dashboard_snapshots"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    cpu_usage_percent = Column(Numeric(5, 2), nullable=True)
    memory_used_mb = Column(Integer, nullable=True)
    memory_total_mb = Column(Integer, nullable=True)
    disk_used_gb = Column(Numeric(8, 2), nullable=True)
    disk_total_gb = Column(Numeric(8, 2), nullable=True)
    active_sse_connections = Column(Integer, nullable=True)
    events_pushed_hour = Column(Integer, nullable=True)
    healthy_sources = Column(Integer, nullable=True)
    degraded_sources = Column(Integer, nullable=True)
    down_sources = Column(Integer, nullable=True)
    scheduler_jobs_active = Column(Integer, nullable=True)
    timestamp = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )

    __table_args__ = (Index("idx_dashboard_snapshots_time", "tenant_id", "timestamp"),)

    tenant = relationship("Tenant")
