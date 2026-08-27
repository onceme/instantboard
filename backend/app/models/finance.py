import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import DATE, UUID
from sqlalchemy.orm import relationship

from app.models.base import Base, BaseModel


class FinanceSymbol(BaseModel):
    __tablename__ = "finance_symbols"

    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol = Column(String(20), nullable=False)
    name = Column(String(200), nullable=False)
    type = Column(String(20), nullable=False)
    market = Column(String(10), nullable=False)
    exchange = Column(String(50), nullable=True)
    currency = Column(String(3), default="USD", server_default=text("'USD'"))
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        UniqueConstraint("tenant_id", "symbol", name="uq_finance_symbols_tenant_symbol"),
        CheckConstraint(
            "type IN ('stock', 'fund', 'index', 'commodity', 'futures', 'currency')",
            name="chk_finance_symbols_type",
        ),
        Index("idx_finance_symbols_type", "tenant_id", "type"),
        Index("idx_finance_symbols_market", "tenant_id", "market"),
    )

    tenant = relationship("Tenant", lazy="selectin")
    quotes = relationship("FinanceQuote", back_populates="symbol", cascade="all, delete-orphan", lazy="noload")
    nav_estimates = relationship(
        "FundNAVEstimate", back_populates="symbol", cascade="all, delete-orphan", lazy="noload"
    )
    watchlist_items = relationship(
        "WatchlistItem", back_populates="symbol", cascade="all, delete-orphan", lazy="noload"
    )


class FinanceQuote(Base):
    """Monthly RANGE-partitioned quote history (database.md §3.1).

    On PostgreSQL this table is created with ``PARTITION BY RANGE (timestamp)``
    (``postgresql_partition_by`` in ``__table_args__``); the concrete month
    partitions are supplied by ``app/db/partitions.py::ensure_quote_partitions``.
    SQLite ignores the dialect option and gets a plain table with the same
    schema.

    PostgreSQL requires the partition key to be part of every unique
    constraint, so the PK is composite ``(id, timestamp)`` instead of id alone.
    Impact: ORM identity lookups (``session.get``) need the full composite key;
    no code fetches quotes by PK — quotes are write-only rows
    (``services/finance.py::_store_quote_to_db``) queried by symbol/time.
    """

    __tablename__ = "finance_quotes"

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
    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("finance_symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    current_price = Column(Numeric(18, 4), nullable=True)
    open_price = Column(Numeric(18, 4), nullable=True)
    high_price = Column(Numeric(18, 4), nullable=True)
    low_price = Column(Numeric(18, 4), nullable=True)
    close_previous = Column(Numeric(18, 4), nullable=True)
    volume = Column(BigInteger, nullable=True)
    change_value = Column(Numeric(18, 4), nullable=True)
    change_percent = Column(Numeric(8, 4), nullable=True)
    market_cap = Column(BigInteger, nullable=True)
    pe_ratio = Column(Numeric(8, 2), nullable=True)
    week_high_52 = Column("52_week_high", Numeric(18, 4), nullable=True)
    week_low_52 = Column("52_week_low", Numeric(18, 4), nullable=True)
    # Partition key on PostgreSQL — must be part of the PK (see class docstring).
    timestamp = Column(DateTime(timezone=True), nullable=False, primary_key=True)
    source_name = Column(String(50), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        Index("idx_finance_quotes_symbol_time", "symbol_id", "timestamp"),
        Index("idx_finance_quotes_tenant", "tenant_id"),
        {"postgresql_partition_by": "RANGE (timestamp)"},
    )

    tenant = relationship("Tenant", lazy="selectin")
    symbol = relationship("FinanceSymbol", back_populates="quotes", lazy="selectin")


class FundNAVEstimate(Base):
    __tablename__ = "fund_nav_estimates"

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
    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("finance_symbols.id", ondelete="CASCADE"),
        nullable=False,
    )
    nav_official = Column(Numeric(18, 4), nullable=True)
    nav_official_date = Column(DATE, nullable=True)
    nav_estimate = Column(Numeric(18, 4), nullable=True)
    nav_estimate_deviation_percent = Column(Numeric(8, 4), nullable=True)
    estimate_method = Column(String(50), nullable=True)
    estimate_timestamp = Column(DateTime(timezone=True), nullable=False)
    underlying_index_symbol = Column(String(20), nullable=True)
    underlying_index_value = Column(Numeric(18, 4), nullable=True)
    underlying_index_change_percent = Column(Numeric(8, 4), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (Index("idx_fund_nav_symbol", "symbol_id", "estimate_timestamp"),)

    tenant = relationship("Tenant", lazy="selectin")
    symbol = relationship("FinanceSymbol", back_populates="nav_estimates", lazy="selectin")
