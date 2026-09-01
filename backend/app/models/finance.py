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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import DATE, UUID
from sqlalchemy.orm import relationship

from app.models.base import Base, BaseModel

# fund_holdings_meta.disclosure_status values (fund-intraday-nav.md §3.2/§5.4):
# "ok" fresh disclosure, "stale" report older than the freshness threshold
# (FUND_NAV_HOLDINGS_FRESH_DAYS), "anomalous" report older than
# FUND_HOLDINGS_ANOMALOUS_DAYS (730) or a zero-holdings/unparsable upstream.
DISCLOSURE_STATUS_OK = "ok"
DISCLOSURE_STATUS_STALE = "stale"
DISCLOSURE_STATUS_ANOMALOUS = "anomalous"

# fund_index_bindings.source values (fund-intraday-nav.md §3.3): rows seeded
# via db/init_db.py vs. rows maintained manually by admins later.
BINDING_SOURCE_SEED = "seed"
BINDING_SOURCE_ADMIN = "admin"


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
    # Holdings-weighted estimate provenance (fund-intraday-nav.md §3.4): the
    # coverage (sum of available holding weights) and the disclosure report
    # date the estimate was computed from; NULL on official / index_tracking
    # rows.
    holdings_coverage_percent = Column(Numeric(8, 4), nullable=True)
    holdings_report_date = Column(DATE, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (Index("idx_fund_nav_symbol", "symbol_id", "estimate_timestamp"),)

    tenant = relationship("Tenant", lazy="selectin")
    symbol = relationship("FinanceSymbol", back_populates="nav_estimates", lazy="selectin")


class FundNAVCalibration(Base):
    """Per-fund estimate calibration (fund-intraday-nav.md §13 M3 §1.1).

    Each night after the official NAV update, the fund's systematic intraday
    estimate bias is learned by comparing recent closing estimates against the
    realized official NAV change, then applied (bounded) to intraday estimates.
    One row per fund. System-tenant scoped like the other fund tables. Sample
    accumulation starts once daily samples exist; calibration is only applied
    once enough samples have accumulated (see FundCalibrationService).
    """

    __tablename__ = "fund_nav_calibration"

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
    fund_code = Column(String(6), nullable=False)
    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("finance_symbols.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Additive estimate bias in percent, bounded to ±50bp before storage.
    additive_bias_percent = Column(Numeric(8, 4), nullable=True)
    # Number of daily samples the bias was computed from.
    sample_count = Column(Integer, nullable=True)
    # Last official NAV/date seen, to support incremental sample pairing.
    last_official_nav = Column(Numeric(18, 4), nullable=True)
    last_official_date = Column(DATE, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint("fund_code", name="uq_fund_nav_calibration_fund_code"),
        Index("idx_fund_nav_calibration_fund_code", "fund_code"),
    )

    tenant = relationship("Tenant", lazy="selectin")


class FundHoldingSnapshot(Base):
    """Latest disclosed top-N holdings snapshot for one fund (fund-intraday-nav.md §3.1).

    System-tenant scoped (SYSTEM_TENANT_ID): holdings are market facts shared
    across all tenants; the intraday NAV job computes the cross-tenant union
    of followed funds once. Replaced wholesale per fund when a newer report
    period is ingested.
    """

    __tablename__ = "fund_holdings_snapshots"

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
    fund_code = Column(String(6), nullable=False)
    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("finance_symbols.id", ondelete="CASCADE"),
        nullable=True,
    )
    report_date = Column(DATE, nullable=False)
    stock_code = Column(String(20), nullable=False)
    stock_name = Column(String(100), nullable=True)
    market = Column(String(10), nullable=False)
    # EastMoney quote secid parsed from the holdings page row link when present
    # (1.600519 Shanghai / 0.000858 Shenzhen / 116.00700 HK / 105.AAPL US);
    # NULL when absent — the quote side then falls back to code-rule mapping.
    secid = Column(String(30), nullable=True)
    weight_percent = Column(Numeric(8, 4), nullable=False)
    shares_held = Column(Numeric(20, 4), nullable=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "fund_code",
            "report_date",
            "stock_code",
            name="uq_fund_holdings_snapshot",
        ),
        Index("idx_fund_holdings_snapshot_fund", "fund_code", "report_date"),
    )

    tenant = relationship("Tenant", lazy="selectin")


class FundHoldingsMeta(Base):
    """Ingestion / disclosure state per fund code (fund-intraday-nav.md §3.2)."""

    __tablename__ = "fund_holdings_meta"

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
    fund_code = Column(String(6), nullable=False)
    symbol_id = Column(
        UUID(as_uuid=True),
        ForeignKey("finance_symbols.id", ondelete="CASCADE"),
        nullable=True,
    )
    latest_report_date = Column(DATE, nullable=True)
    # latest report period's weight sum = the coverage numerator for the
    # holdings-weighted estimator (fund-intraday-nav.md §3.2).
    top10_weight_sum = Column(Numeric(8, 4), nullable=True)
    holdings_count = Column(Integer, nullable=True)
    disclosure_status = Column(String(20), nullable=True)
    last_fetched_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("fund_code", name="uq_fund_holdings_meta_fund_code"),
        Index("idx_fund_holdings_meta_code", "fund_code"),
    )

    tenant = relationship("Tenant", lazy="selectin")


class FundIndexBinding(Base):
    """Fund → tracking index binding (fund-intraday-nav.md §3.3).

    Fixes the underlying_index_symbol dead-end: fund_nav_estimates only ever
    copied a binding back from a previous row and no code path seeded one, so
    the index-tracking estimate branch never engaged for new funds. This table
    is the auditable source of truth (seeded from db/init_db.py, manually
    maintainable later); the estimate path reads bindings here first and falls
    back to a residual underlying_index_symbol row for legacy data.
    """

    __tablename__ = "fund_index_bindings"

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
    fund_code = Column(String(6), nullable=False)
    index_symbol = Column(String(20), nullable=False)
    tracking_ratio = Column(Numeric(6, 4), nullable=False, default=1.0, server_default=text("1.0"))
    source = Column(
        String(20),
        nullable=False,
        default=BINDING_SOURCE_SEED,
        server_default=text("'seed'"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )

    __table_args__ = (
        UniqueConstraint("fund_code", name="uq_fund_index_bindings_fund_code"),
        Index("idx_fund_index_bindings_code", "fund_code"),
    )

    tenant = relationship("Tenant", lazy="selectin")
