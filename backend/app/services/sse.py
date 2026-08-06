import logging
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

# Fix: default tenant of publish_source_health_update changed from "system" to
# SYSTEM_TENANT_ID (kept as str for SSE JSON serialization).
from app.core.constants import SYSTEM_TENANT_ID
from app.core.sse_router import SSEEventType, event_router
from app.models.sse import SSEConnection as SSEConnectionModel

logger = logging.getLogger(__name__)


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
        conn = event_router.register(client_id, categories, tenant_id)

        db_conn = SSEConnectionModel(
            tenant_id=tenant_id,
            user_id=user_id,
            channels=categories,
            connected_at=datetime.now(UTC),
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

        await db_session.execute(
            update(SSEConnectionModel)
            .where(
                SSEConnectionModel.user_id == conn.tenant_id,
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
        source_id: str,
        status: str,
        last_error: str | None = None,
        tenant_id: str = str(SYSTEM_TENANT_ID),
    ) -> None:
        data = {
            "source_id": source_id,
            "status": status,
            "last_error": last_error,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        await event_router.push_event(
            category="dashboard",
            event_type=SSEEventType.SOURCE_HEALTH_UPDATE,
            data=data,
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
