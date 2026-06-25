import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.models.base import Base


class SSEConnection(Base):
    __tablename__ = "sse_connections"

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
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    channels = Column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    connected_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("NOW()"),
    )
    disconnected_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at = Column(DateTime(timezone=True), nullable=True)
    client_ip = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)

    __table_args__ = (
        Index("idx_sse_connections_user", "user_id"),
        Index(
            "idx_sse_connections_active",
            "disconnected_at",
            postgresql_where=Column("disconnected_at").is_(None),
        ),
    )

    tenant = relationship("Tenant", lazy="selectin")
    user = relationship("User", back_populates="sse_connections", lazy="selectin")
