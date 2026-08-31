"""repair fund intraday nav schema gap

Idempotent repair for the fund intraday NAV schema introduced by
c3f2a8d1e9b4 (fund-intraday-nav.md §3).

Root cause of the drift this fixes: c3f2a8d1e9b4 was *marked applied* in the
staging database's alembic_version, but its two ``ALTER TABLE ... ADD COLUMN``
steps never physically executed. The most likely history is that a prior
``alembic upgrade head`` failed mid-way and the entrypoint fell back to
``Base.metadata.create_all()``. create_all happily creates tables that do not
exist, but — this is the trap — it never ALTERs a table it finds already
present. ``fund_nav_estimates`` already existed, so create_all left it without
``holdings_coverage_percent`` / ``holdings_report_date`` while still creating
the three brand-new tables. Every subsequent ``alembic upgrade head`` then saw
the version already at head and became a no-op, so the two columns were never
added and every ORM read of ``FundNAVEstimate`` raised
``UndefinedColumnError`` → HTTP 500 on the fund-NAV endpoints.

Because alembic_version is already stamped at/past c3f2a8d1e9b4, a *new*
chained revision is the only thing ``upgrade head`` will actually execute;
this migration converges the schema from whatever partial state it finds:

- ``ADD COLUMN IF NOT EXISTS`` for the two fund_nav_estimates provenance
  columns;
- ``CREATE TABLE IF NOT EXISTS`` for the three new tables (no-op if present);
- ``CREATE INDEX IF NOT EXISTS`` for their indexes (no-op if present);
- the shared ``tenant_isolation`` RLS treatment for the three tables
  (app.db.rls.rls_ddl_statements), re-applied idempotently.

Non-PostgreSQL dialects (SQLite dev/test) are a no-op: on a fresh SQLite
database c3f2a8d1e9b4 already created the tables/columns correctly, and the
partial-application failure mode this repairs is PostgreSQL-specific.

The downgrade is deliberately a no-op: the objects this migration creates are
owned by c3f2a8d1e9b4's schema, and undoing them here would either double-drop
or corrupt a healthy database on a later ``downgrade`` of c3f2a8d1e9b4. Down
semantics are provided by c3f2a8d1e9b4's own downgrade().

Revision ID: e5a9c4f7b2d1
Revises: c3f2a8d1e9b4
Create Date: 2026-08-31 23:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.rls import rls_ddl_statements

# revision identifiers, used by Alembic.
revision: str = "e5a9c4f7b2d1"
down_revision: str | Sequence[str] | None = "c3f2a8d1e9b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_TABLES = ("fund_holdings_snapshots", "fund_holdings_meta", "fund_index_bindings")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite dev/test databases created the schema correctly via
        # c3f2a8d1e9b4; nothing to repair there.
        return

    # --- 1. The two fund_nav_estimates provenance columns -------------------
    # IF NOT EXISTS makes this converge whether the column is present or not.
    op.execute(
        sa.text("ALTER TABLE fund_nav_estimates ADD COLUMN IF NOT EXISTS holdings_coverage_percent NUMERIC(8, 4)")
    )
    op.execute(sa.text("ALTER TABLE fund_nav_estimates ADD COLUMN IF NOT EXISTS holdings_report_date DATE"))

    # --- 2. The three new tables (no-op if they already exist) -------------
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

    # --- 3. Indexes (no-op if present) -------------------------------------
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS idx_fund_holdings_snapshot_fund "
            "ON fund_holdings_snapshots (fund_code, report_date)"
        )
    )
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_fund_holdings_meta_code ON fund_holdings_meta (fund_code)"))
    op.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_fund_index_bindings_code ON fund_index_bindings (fund_code)"))

    # --- 4. RLS treatment for the three tables (idempotent) ----------------
    # rls_ddl_statements is ENABLE + FORCE + DROP POLICY IF EXISTS + CREATE
    # POLICY, all of which are safe to re-run on a table that already has them.
    for table in _NEW_TABLES:
        for statement in rls_ddl_statements(table):
            op.execute(statement)


def downgrade() -> None:
    # Deliberate no-op. The objects this migration guarantees are owned by
    # c3f2a8d1e9b4's schema; dropping them here would double-drop on a later
    # downgrade of c3f2a8d1e9b4 and could corrupt a healthy database. Down
    # semantics intentionally live in c3f2a8d1e9b4.downgrade().
    pass
