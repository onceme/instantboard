from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

# Fix: default tenant of publish_source_health_update changed from "system" to
# SYSTEM_TENANT_ID (kept as str for SSE JSON serialization).
from app.core.constants import SYSTEM_TENANT_ID
from app.core.sse_router import SSEEventType, event_router
from app.models.sse import SSEConnection as SSEConnectionModel

if TYPE_CHECKING:
    from app.models.source import Source, SourceHealth

logger = logging.getLogger(__name__)


def build_source_health_update_payload(source: Source | Any, health: SourceHealth | Any, previous_status: str) -> dict:
    """Build the canonical source_health_update SSE payload.

    Contract: docs/design/data-flow.md §3.5.4. The payload carries the full
    source_health row state displayed by the dashboard's DataSourcesHealth table
    (read from the SourceHealth record plus the owning Source) so the frontend
    can match the table row by source_id and refresh it in place.
    """
    success_rate_24h = None
    if health.total_fetches_24h:
        success_rate_24h = health.success_count_24h / health.total_fetches_24h
    return {
        "source_id": str(source.id),
        "name": source.name,
        "source_type": source.source_type,
        "status": health.status,
        "previous_status": previous_status,
        "last_error": health.last_error_message,
        "last_success_at": health.last_success_at.isoformat() if health.last_success_at else None,
        "last_failure_at": health.last_failure_at.isoformat() if health.last_failure_at else None,
        "avg_response_time_ms": health.avg_response_time_ms,
        "consecutive_failures": health.consecutive_failures,
        "success_count_24h": health.success_count_24h,
        "total_fetches_24h": health.total_fetches_24h,
        "success_rate_24h": success_rate_24h,
        "timestamp": datetime.now(UTC).isoformat(),
    }


class SSEService:
    async def connect(
        self,
        client_id: str,
        categories: list[str],
        tenant_id: str,
        user_id: str,
        db_session: AsyncSession,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> dict:
        conn = event_router.register(client_id, categories, tenant_id, user_id=user_id)

        db_conn = SSEConnectionModel(
            tenant_id=tenant_id,
            user_id=user_id,
            channels=categories,
            # Reuse the in-memory connection's timestamp so disconnect() can match the
            # exact row via (user_id, connected_at); a fresh now() here would differ.
            connected_at=conn.connected_at,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        db_session.add(db_conn)
        await db_session.commit()

        return {
            "client_id": client_id,
            "categories": categories,
            "tenant_id": tenant_id,
            "connected_at": conn.connected_at.isoformat(),
        }

    async def disconnect(self, client_id: str, db_session: AsyncSession) -> dict | None:
        conn = event_router.unregister(client_id)
        if conn is None:
            return None

        # Audit the disconnect against the exact row written by connect(): the previous
        # WHERE compared user_id == conn.tenant_id (always false), so rows were never
        # marked disconnected. Match on (user_id, connected_at) with an open session.
        await db_session.execute(
            update(SSEConnectionModel)
            .where(
                SSEConnectionModel.user_id == conn.user_id,
                SSEConnectionModel.connected_at == conn.connected_at,
                SSEConnectionModel.disconnected_at.is_(None),
            )
            .values(
                disconnected_at=datetime.now(UTC),
            )
        )
        await db_session.commit()

        return {
            "client_id": client_id,
            "events_sent": conn.events_sent_count,
            "duration_seconds": (datetime.now(UTC) - conn.connected_at).total_seconds(),
        }

    async def get_connection_status(self, client_id: str) -> dict | None:
        conn = event_router.get_connection(client_id)
        if conn is None:
            return None
        return {
            "client_id": conn.client_id,
            "categories": conn.categories,
            "tenant_id": conn.tenant_id,
            "is_active": conn.is_active,
            "connected_at": conn.connected_at.isoformat(),
            "last_event_at": conn.last_event_at.isoformat() if conn.last_event_at else None,
            "events_sent": conn.events_sent_count,
        }

    async def get_all_connections(self, tenant_id: str) -> list[dict]:
        connections = []
        for conn in event_router.get_all_active_connections():
            if conn.tenant_id == tenant_id:
                connections.append(
                    {
                        "client_id": conn.client_id,
                        "categories": conn.categories,
                        "connected_at": conn.connected_at.isoformat(),
                        "events_sent": conn.events_sent_count,
                    }
                )
        return connections

    async def get_sse_stats(self) -> dict:
        return event_router.get_stats()

    async def publish_source_health_update(
        self,
        payload: dict,
        tenant_id: str = str(SYSTEM_TENANT_ID),
    ) -> None:
        """Publish a source_health_update event on the dashboard channel.

        Contract: docs/design/data-flow.md §3.5.4 — the payload must be the
        full row state built by build_source_health_update_payload() (keyed by
        source_id). Tenant routing: event_router only delivers the event to SSE
        connections registered with the same tenant_id; admin sessions belong to
        the system tenant, so events for system-tenant sources published with
        SYSTEM_TENANT_ID reach them.
        """
        await event_router.push_event(
            category="dashboard",
            event_type=SSEEventType.SOURCE_HEALTH_UPDATE,
            data=payload,
            tenant_id=tenant_id,
        )

    async def publish_item_update(
        self,
        category: str,
        item_data: dict,
        tenant_id: str,
    ) -> None:
        await event_router.push_event(
            category=category,
            event_type=SSEEventType.ITEM_UPDATE,
            data=item_data,
            tenant_id=tenant_id,
        )

    async def publish_quote_update(
        self,
        symbol: str,
        quote_data: dict,
        tenant_id: str,
    ) -> None:
        await event_router.push_event(
            category="finance",
            event_type=SSEEventType.QUOTE_UPDATE,
            data={"symbol": symbol, **quote_data},
            tenant_id=tenant_id,
        )

    async def publish_market_index_update(
        self,
        indices_data: list[dict],
        tenant_id: str,
    ) -> None:
        await event_router.push_event(
            category="finance",
            event_type=SSEEventType.MARKET_INDEX_UPDATE,
            data={"indices": indices_data},
            tenant_id=tenant_id,
        )

    async def publish_commodity_update(
        self,
        commodities_data: list[dict],
        tenant_id: str,
    ) -> None:
        await event_router.push_event(
            category="finance",
            event_type=SSEEventType.COMMODITY_UPDATE,
            data={"commodities": commodities_data},
            tenant_id=tenant_id,
        )
