import logging

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.rls import SERVICE_GUC, TENANT_GUC, bind_service_context, is_postgres_session

logger = logging.getLogger("instantboard")

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
    pool_recycle=settings.database_pool_recycle,
    pool_pre_ping=True,
    echo=settings.is_development,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

if engine.dialect.name == "postgresql":
    # RLS GUC scrub (database.md §3.5): connections are pooled and the
    # app.current_tenant_id / app.is_service settings outlive transactions, so
    # every connection going back to the pool is reset to a GUC-less state.
    # Without this, a tenant GUC (or, far worse, app.is_service=on from a
    # background session) could leak into the next request that checks out the
    # same connection. Defense in depth on top of the per-session wiring.
    _RLS_SCRUB_SQL = f"SELECT set_config('{TENANT_GUC}', '', false), set_config('{SERVICE_GUC}', '', false)"

    @event.listens_for(engine.sync_engine, "checkin")
    def _scrub_rls_context(dbapi_connection, connection_record) -> None:
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(_RLS_SCRUB_SQL)
            finally:
                cursor.close()
        except Exception as exc:
            # A failed scrub must not break connection release; the next
            # session re-applies its own GUC before running any query, and
            # invalid connections are recycled by pool_pre_ping.
            logger.warning(f"RLS GUC scrub on pool checkin failed: {exc}")


async def apply_service_context(session: AsyncSession) -> None:
    """Mark ``session`` as a server-side background session for RLS purposes.

    Binds the ``app.is_service=on`` bypass (force-applied at every transaction
    begin, see app/db/rls.py for why transaction-local): background paths
    (scheduler collection, dashboard metrics/archive, seeding, ...) have no
    request context and must not be fenced off by the tenant policies.

    Trust boundary: this is the ONLY code path allowed to switch on the RLS
    bypass; it must never run for request sessions (get_db) — the per-request
    context there is app.current_tenant_id only.

    Non-PostgreSQL dialects (SQLite dev/test) have no RLS: this is a no-op.
    Call this immediately after opening the session, before the first query.
    """
    try:
        if not is_postgres_session(session):
            return
        bind_service_context(session)
    except Exception as exc:
        # Never block the background task because of context wiring; on
        # PostgreSQL this only fails if the session is misconfigured, in
        # which case RLS simply hides the rows and the task errors loudly.
        logger.warning(f"apply_service_context failed: {exc}")


async def get_db_session() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()
