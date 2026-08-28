"""tenancy row level security

Enable PostgreSQL Row-Level Security on every tenant-scoped business table as
a defense-in-depth backstop behind the application-layer ``tenant_id`` filters
(database.md §3.5 / security.md §3.5).

Design (single database role == table owner, so FORCE is mandatory — without
it the owner would bypass every policy and RLS would be decorative):

- ``ENABLE ROW LEVEL SECURITY`` + ``FORCE ROW LEVEL SECURITY`` on each table.
- One permissive policy ``tenant_isolation`` per table:
    USING        service bypass OR own tenant OR (system tenant, read-only)
    WITH CHECK   service bypass OR own tenant
  ``app.is_service=on`` is set exclusively by server-side background sessions
  (``app/db/session.py::apply_service_context``); request sessions only ever
  set ``app.current_tenant_id``. The SYSTEM tenant clause (USING only) keeps
  the shared seeded categories/sources/items readable by every tenant;
  ``NULLIF(...,'')`` protects the pool-checkin GUC reset from ``''::uuid``.
  The expression is built from ``app/db/rls.py`` constants — the runtime
  context wiring and this migration can never drift.

- ``finance_quotes`` is RANGE-partitioned: partitions do NOT inherit the
  parent's ``relrowsecurity``/``relforcerowsecurity`` flags (verified against
  PostgreSQL — direct partition access bypassed the parent policy), so every
  existing partition gets the same ENABLE + FORCE + POLICY. Partitions created
  later are covered by ``app/db/partitions.py::ensure_quote_partitions``.

Deliberately excluded (see database.md §3.5): ``users`` (login lookups happen
before any tenant context exists) and ``dashboard_snapshots`` (global ops
data, admin-only reads, already app-filtered). ``tenants`` and
``source_health`` have no ``tenant_id`` column.

Non-PostgreSQL dialects (SQLite dev/test): no-op.

Revision ID: 7d9a46a0d5c9
Revises: bb1a61d49502
Create Date: 2026-08-27 11:28:38.140048

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.rls import RLS_TABLES, TENANT_ISOLATION_POLICY, rls_ddl_statements

# revision identifiers, used by Alembic.
revision: str = "7d9a46a0d5c9"
down_revision: str | Sequence[str] | None = "bb1a61d49502"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_QUOTE_PARENT = "finance_quotes"

# to_regclass() instead of a ':parent::regclass' cast: asyncpg rejects bind
# parameters immediately followed by a cast.
_EXISTING_QUOTE_PARTITIONS_SQL = sa.text(
    "SELECT c.relname FROM pg_class c "
    "JOIN pg_inherits i ON i.inhrelid = c.oid "
    "WHERE i.inhparent = to_regclass(:parent) "
    "ORDER BY c.relname"
)


def _existing_quote_partitions(bind) -> list[str]:
    return [row[0] for row in bind.execute(_EXISTING_QUOTE_PARTITIONS_SQL, {"parent": _QUOTE_PARENT})]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table in RLS_TABLES:
        for statement in rls_ddl_statements(table):
            op.execute(statement)

    # finance_quotes partitions existing at migration time need the policy
    # individually (see module docstring). Monthly partitions created after
    # this migration are wired by app/db/partitions.py at CREATE time.
    for partition in _existing_quote_partitions(bind):
        for statement in rls_ddl_statements(partition):
            op.execute(statement)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Reverse of upgrade: partitions first, then the 8 tables.
    for partition in _existing_quote_partitions(bind):
        op.execute(f"DROP POLICY IF EXISTS {TENANT_ISOLATION_POLICY} ON {partition}")
        op.execute(f"ALTER TABLE {partition} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {partition} DISABLE ROW LEVEL SECURITY")

    for table in reversed(RLS_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {TENANT_ISOLATION_POLICY} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
