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

Idempotency note (added after a staging incident): this revision is written
with ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` /
``ADD COLUMN IF NOT EXISTS`` so it converges from a partially-applied state.
Observed staging history: an earlier ``alembic upgrade head`` failed and the
entrypoint fell back to ``Base.metadata.create_all()``, which created the
three new tables but never added the two ``fund_nav_estimates`` columns
(create_all does not ALTER existing tables). With alembic_version still at
7d9a46a0d5c9, re-running this revision then failed with "relation ... already
exists" and the chain could not advance, so the columns were never added and
the fund-NAV endpoints 500'd with UndefinedColumnError. The IF NOT EXISTS
forms let this revision succeed whether or not the objects already exist, so
the chain reaches head (and the follow-up repair revision e5a9c4f7b2d1).

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


def _column_exists(bind, table: str, column: str) -> bool:
    """True when `column` already exists on `table` (dialect-aware)."""
    if bind.dialect.name == "postgresql":
        result = bind.execute(
            sa.text("SELECT 1 FROM information_schema.columns WHERE table_name = :table AND column_name = :column"),
            {"table": table, "column": column},
        )
        return result.first() is not None
    # SQLite: consult pragma_table_info.
    result = bind.execute(sa.text(f"PRAGMA table_info({table})"))
    return any(row[1] == column for row in result)


def upgrade() -> None:
    bind = op.get_bind()

    # --- 1. The three new tables (IF NOT EXISTS → converge from any state) ---
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS fund_holdings_snapshots (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                fund_code VARCHAR(6) NOT NULL,
                symbol_id UUID REFERENCES finance_symbols(id) ON DELETE CASCADE,
                report_date DATE NOT NULL,
                stock_code VARCHAR(20) NOT NULL,
                stock_name VARCHAR(100),
                market VARCHAR(10) NOT NULL,
                secid VARCHAR(30),
                weight_percent NUMERIC(8, 4) NOT NULL,
                shares_held NUMERIC(20, 4),
                fetched_at TIMESTAMP WITH TIME ZONE NOT NULL,
                CONSTRAINT uq_fund_holdings_snapshot UNIQUE (fund_code, report_date, stock_code)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS fund_holdings_meta (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                fund_code VARCHAR(6) NOT NULL,
                symbol_id UUID REFERENCES finance_symbols(id) ON DELETE CASCADE,
                latest_report_date DATE,
                top10_weight_sum NUMERIC(8, 4),
                holdings_count INTEGER,
                disclosure_status VARCHAR(20),
                last_fetched_at TIMESTAMP WITH TIME ZONE,
                last_error TEXT,
                CONSTRAINT uq_fund_holdings_meta_fund_code UNIQUE (fund_code)
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TABLE IF NOT EXISTS fund_index_bindings (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
                fund_code VARCHAR(6) NOT NULL,
                index_symbol VARCHAR(20) NOT NULL,
                tracking_ratio NUMERIC(6, 4) NOT NULL DEFAULT 1.0,
                source VARCHAR(20) NOT NULL DEFAULT 'seed',
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_fund_index_bindings_fund_code UNIQUE (fund_code)
            )
            """
        )
    )

    # --- 2. Indexes (IF NOT EXISTS → no-op if present) ----------------------
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_fund_holdings_snapshot_fund "
            "ON fund_holdings_snapshots (fund_code, report_date)"
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_fund_holdings_meta_code ON fund_holdings_meta (fund_code)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_fund_index_bindings_code ON fund_index_bindings (fund_code)"))

    # --- 3. fund_nav_estimates provenance columns ---------------------------
    # PostgreSQL has native ADD COLUMN IF NOT EXISTS; SQLite has no IF NOT
    # EXISTS clause for ADD COLUMN, so guard with an existence check first.
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text("ALTER TABLE fund_nav_estimates ADD COLUMN IF NOT EXISTS holdings_coverage_percent NUMERIC(8, 4)")
        )
        op.execute(sa.text("ALTER TABLE fund_nav_estimates ADD COLUMN IF NOT EXISTS holdings_report_date DATE"))
    else:
        if not _column_exists(bind, "fund_nav_estimates", "holdings_coverage_percent"):
            op.add_column(
                "fund_nav_estimates",
                sa.Column("holdings_coverage_percent", sa.Numeric(precision=8, scale=4), nullable=True),
            )
        if not _column_exists(bind, "fund_nav_estimates", "holdings_report_date"):
            op.add_column("fund_nav_estimates", sa.Column("holdings_report_date", sa.DATE(), nullable=True))

    # --- 4. RLS for the new tenant-scoped tables (idempotent) ---------------
    # Identical ENABLE + FORCE + tenant_isolation policy as the other 8 tables.
    # SQLite and other non-PostgreSQL dialects skip this, same as the RLS
    # migration. rls_ddl_statements is safe to re-run (DROP POLICY IF EXISTS +
    # CREATE POLICY, ENABLE/FORCE are idempotent).
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
