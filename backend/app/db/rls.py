"""PostgreSQL Row-Level Security context wiring (database.md §3.5).

RLS is the defense-in-depth backstop behind the application-layer
``tenant_id`` filters: every request session carries the caller's tenant via
the ``app.current_tenant_id`` GUC, every server-side background session is
marked with ``app.is_service=on`` (see ``apply_service_context`` in
``app/db/session.py``).

The policy expression mirrors the migration
``app/alembic/versions/*_tenancy_row_level_security.py`` — keep the two in
sync when either changes. Semantics:

- ``app.is_service = 'on'``  → full access (server-side background tasks).
  Trust boundary: only server code calls ``apply_service_context``; there is
  no request path that ever sets this GUC.
- ``app.current_tenant_id``  → rows of that tenant (USING/WITH CHECK).
- SYSTEM tenant rows are readable (USING only) by any session that carries a
  tenant GUC: seeded categories/sources/items belong to the fixed system
  tenant and are shared with every tenant by design (``or_(tenant_id ==
  tenant, tenant_id == SYSTEM_TENANT_ID)`` all over the service layer).
  Writes to system-tenant rows stay reserved to service sessions.
- No GUC at all → zero tenant rows visible (default deny).

``NULLIF(current_setting(...), '')`` guards the reset path: pooled connections
are scrubbed to ``''`` on checkin, and ``''::uuid`` would raise instead of
hiding rows.

The GUC is applied with ``is_local=TRUE`` (transaction-local) via an
``after_begin`` session event: SQLAlchemy releases the session's connection
back to the pool on every ``commit()``, so a session-scoped
(``is_local=FALSE``) setting would silently disappear mid-request after the
first commit and, worse, linger on the pooled connection for the next user
of that connection. Transaction-local + re-apply on every transaction begin
plus the pool-checkin scrub in ``app/db/session.py`` guarantee both no leaks
across requests and a valid GUC for every statement.
"""

import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("instantboard")

TENANT_GUC = "app.current_tenant_id"
SERVICE_GUC = "app.is_service"
SERVICE_ON = "on"
TENANT_ISOLATION_POLICY = "tenant_isolation"

# The 8 tables carrying tenant-scoped business data (models cross-checked;
# see database.md §3.5 for why ``users`` and ``dashboard_snapshots`` are
# deliberately excluded). ``finance_quotes`` is a partitioned parent: its
# partitions need the same DDL individually (they have their own
# relrowsecurity/relforcerls flags).
RLS_TABLES: tuple[str, ...] = (
    "categories",
    "sources",
    "items",
    "finance_symbols",
    "finance_quotes",
    "fund_nav_estimates",
    "watchlist_items",
    "sse_connections",
)

# Fixed UUID of the shared system tenant (app/core/constants.py). Hardcoded
# here because the policy SQL must not depend on Python constants at runtime.
SYSTEM_TENANT_ID_SQL = "'00000000-0000-0000-0000-000000000000'::uuid"

TENANT_CLAUSE = f"tenant_id = NULLIF(current_setting('{TENANT_GUC}', true), '')::uuid"
SYSTEM_READ_CLAUSE = (
    f"(NULLIF(current_setting('{TENANT_GUC}', true), '') IS NOT NULL AND tenant_id = {SYSTEM_TENANT_ID_SQL})"
)
SERVICE_CLAUSE = f"current_setting('{SERVICE_GUC}', true) = '{SERVICE_ON}'"

RLS_USING_EXPRESSION = f"{SERVICE_CLAUSE} OR {TENANT_CLAUSE} OR {SYSTEM_READ_CLAUSE}"
RLS_WITH_CHECK_EXPRESSION = f"{SERVICE_CLAUSE} OR {TENANT_CLAUSE}"


def rls_ddl_statements(table: str) -> list[str]:
    """Idempotent DDL enabling RLS + the tenant_isolation policy on one table."""
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {TENANT_ISOLATION_POLICY} ON {table}",
        (
            f"CREATE POLICY {TENANT_ISOLATION_POLICY} ON {table} "
            f"USING ({RLS_USING_EXPRESSION}) "
            f"WITH CHECK ({RLS_WITH_CHECK_EXPRESSION})"
        ),
    ]


def is_postgres_session(session: AsyncSession) -> bool:
    bind = session.bind
    return bind is not None and bind.dialect.name == "postgresql"


def bind_tenant_context(session: AsyncSession, tenant_id: str) -> None:
    """Re-apply ``app.current_tenant_id`` at the start of every transaction.

    Request sessions only (``dependencies.get_db``): never call this for
    background work — that goes through ``apply_service_context``.
    """

    def _after_begin(session_, transaction, connection) -> None:
        connection.execute(
            text(f"SELECT set_config('{TENANT_GUC}', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )

    event.listen(session.sync_session, "after_begin", _after_begin)


def bind_service_context(session: AsyncSession) -> None:
    """Re-apply ``app.is_service=on`` at the start of every transaction.

    Background sessions only (scheduler, metrics/archive loop, seeding).
    Trust boundary: only server-side code paths call this; no request session
    may ever receive the service bypass (database.md §3.5).
    """

    def _after_begin(session_, transaction, connection) -> None:
        connection.execute(
            text(f"SELECT set_config('{SERVICE_GUC}', :service, true)"),
            {"service": SERVICE_ON},
        )

    event.listen(session.sync_session, "after_begin", _after_begin)
