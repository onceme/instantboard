"""fund intraday nav

Fund intraday NAV support (fund-intraday-nav.md §3):

- ``fund_holdings_snapshots``: latest disclosed top-N holdings per fund
  (system tenant, replaced wholesale when a newer report period arrives).
- ``fund_holdings_meta``: ingestion / disclosure state per fund code.
- ``fund_index_bindings``: fund → tracking index bindings, the auditable
  source of truth that fixes the ``underlying_index_symbol`` dead-end in
  ``fund_nav_estimates``.
- ``fund_nav_estimates`` gains ``holdings_coverage_percent`` +
  ``holdings_report_date`` (provenance of holdings_weighted estimates).

All three new tables carry ``tenant_id`` and get the same RLS treatment as
the other tenant-scoped tables: ENABLE + FORCE ROW LEVEL SECURITY and the
shared ``tenant_isolation`` policy (app.db.rls.rls_ddl_statements), applied
here because the tables only exist after this migration — the tenancy RLS
migration ran before them. Non-PostgreSQL dialects (SQLite dev/test) get the
plain tables without RLS, matching the baseline/RLS migration behavior.

Revision ID: c3f2a8d1e9b4
Revises: 7d9a46a0d5c9
Create Date: 2026-08-31 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.rls import rls_ddl_statements

# revision identifiers, used by Alembic.
revision: str = "c3f2a8d1e9b4"
down_revision: str | Sequence[str] | None = "7d9a46a0d5c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TABLES = ("fund_holdings_snapshots", "fund_holdings_meta", "fund_index_bindings")


def upgrade() -> None:
    op.create_table(
        "fund_holdings_snapshots",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("fund_code", sa.String(length=6), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=True),
        sa.Column("report_date", sa.DATE(), nullable=False),
        sa.Column("stock_code", sa.String(length=20), nullable=False),
        sa.Column("stock_name", sa.String(length=100), nullable=True),
        sa.Column("market", sa.String(length=10), nullable=False),
        sa.Column("secid", sa.String(length=30), nullable=True),
        sa.Column("weight_percent", sa.Numeric(precision=8, scale=4), nullable=False),
        sa.Column("shares_held", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["finance_symbols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fund_code", "report_date", "stock_code", name="uq_fund_holdings_snapshot"),
    )
    op.create_index(
        "idx_fund_holdings_snapshot_fund", "fund_holdings_snapshots", ["fund_code", "report_date"], unique=False
    )

    op.create_table(
        "fund_holdings_meta",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("fund_code", sa.String(length=6), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=True),
        sa.Column("latest_report_date", sa.DATE(), nullable=True),
        sa.Column("top10_weight_sum", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("holdings_count", sa.Integer(), nullable=True),
        sa.Column("disclosure_status", sa.String(length=20), nullable=True),
        sa.Column("last_fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["symbol_id"], ["finance_symbols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fund_code", name="uq_fund_holdings_meta_fund_code"),
    )
    op.create_index("idx_fund_holdings_meta_code", "fund_holdings_meta", ["fund_code"], unique=False)

    op.create_table(
        "fund_index_bindings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("fund_code", sa.String(length=6), nullable=False),
        sa.Column("index_symbol", sa.String(length=20), nullable=False),
        sa.Column("tracking_ratio", sa.Numeric(precision=6, scale=4), server_default=sa.text("1.0"), nullable=False),
        sa.Column("source", sa.String(length=20), server_default=sa.text("'seed'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fund_code", name="uq_fund_index_bindings_fund_code"),
    )
    op.create_index("idx_fund_index_bindings_code", "fund_index_bindings", ["fund_code"], unique=False)

    op.add_column(
        "fund_nav_estimates", sa.Column("holdings_coverage_percent", sa.Numeric(precision=8, scale=4), nullable=True)
    )
    op.add_column("fund_nav_estimates", sa.Column("holdings_report_date", sa.DATE(), nullable=True))

    # RLS for the new tenant-scoped tables (see module docstring): identical
    # ENABLE + FORCE + tenant_isolation policy as the other 8 tables. SQLite
    # and other non-PostgreSQL dialects skip this, same as the RLS migration.
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in _NEW_TABLES:
            for statement in rls_ddl_statements(table):
                op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in reversed(_NEW_TABLES):
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_column("fund_nav_estimates", "holdings_report_date")
    op.drop_column("fund_nav_estimates", "holdings_coverage_percent")

    op.drop_index("idx_fund_index_bindings_code", table_name="fund_index_bindings")
    op.drop_table("fund_index_bindings")
    op.drop_index("idx_fund_holdings_meta_code", table_name="fund_holdings_meta")
    op.drop_table("fund_holdings_meta")
    op.drop_index("idx_fund_holdings_snapshot_fund", table_name="fund_holdings_snapshots")
    op.drop_table("fund_holdings_snapshots")
