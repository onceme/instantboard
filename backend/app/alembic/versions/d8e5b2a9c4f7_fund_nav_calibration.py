"""fund nav calibration

Adds the per-fund estimate calibration table (fund-intraday-nav.md §13 M3
§1.1). Each night after the official NAV update the fund's systematic intraday
estimate bias is learned and applied (bounded) to intraday estimates. One row
per fund, system-tenant scoped.

The table carries tenant_id and gets the same RLS treatment as the other
tenant-scoped fund tables: ENABLE + FORCE ROW LEVEL SECURITY and the shared
tenant_isolation policy (app.db.rls.rls_ddl_statements), applied here because
the table only exists after this migration. Non-PostgreSQL dialects (SQLite
dev/test) are a no-op for the RLS part.

Revision ID: d8e5b2a9c4f7
Revises: e5a9c4f7b2d1
Create Date: 2026-09-01 08:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.rls import rls_ddl_statements

# revision identifiers, used by Alembic.
revision: str = "d8e5b2a9c4f7"
down_revision: str | Sequence[str] | None = "e5a9c4f7b2d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "fund_nav_calibration",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("fund_code", sa.String(length=6), nullable=False),
        sa.Column("symbol_id", sa.UUID(), nullable=True),
        sa.Column("additive_bias_percent", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("last_official_nav", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("last_official_date", sa.DATE(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("NOW()"), nullable=False),
        sa.ForeignKeyConstraint(["symbol_id"], ["finance_symbols.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fund_code", name="uq_fund_nav_calibration_fund_code"),
    )
    op.create_index("idx_fund_nav_calibration_fund_code", "fund_nav_calibration", ["fund_code"], unique=False)

    if bind.dialect.name == "postgresql":
        for statement in rls_ddl_statements("fund_nav_calibration"):
            op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON fund_nav_calibration")
        op.execute("ALTER TABLE fund_nav_calibration NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE fund_nav_calibration DISABLE ROW LEVEL SECURITY")

    op.drop_index("idx_fund_nav_calibration_fund_code", table_name="fund_nav_calibration")
    op.drop_table("fund_nav_calibration")
