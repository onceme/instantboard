import uuid
from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.models.base import Base


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

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
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("finance_symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    display_order = Column(Integer, nullable=False, default=0, server_default="0")
    notes = Column(String(200), nullable=True)
    alert_threshold_percent = Column(Numeric(8, 4), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "symbol_id", name="uq_watchlist_user_symbol"),
        Index("idx_watchlist_user", "user_id", "display_order"),
    )

    tenant = relationship("Tenant")
    user = relationship("User", back_populates="watchlist_items")
    symbol = relationship("FinanceSymbol", back_populates="watchlist_items")
