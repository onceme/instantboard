import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import InvalidToken
from app.core.security import extract_user_from_token
from app.core.sse_router import event_router
from app.dependencies import get_db, get_optional_token
from app.schemas.base import SuccessResponse
from app.services.sse import SSEService

router = APIRouter()
sse_service = SSEService()


@router.get("/{category}")
async def sse_stream(
    category: str,
    token: str = Query(..., description="JWT token for SSE authentication"),
    db: AsyncSession = Depends(get_db),
):
    user_info = extract_user_from_token(token)
    if user_info is None:
        raise InvalidToken()

    user_id = user_info.get("user_id", "anonymous")
    tenant_id = user_info.get("tenant_id", "default")
    client_id = f"{tenant_id}:{user_id}:{uuid.uuid4()}"

    await sse_service.connect(
        client_id=client_id,
        categories=[category],
        tenant_id=tenant_id,
        user_id=user_id,
        db_session=db,
    )

    conn = event_router.get_connection(client_id)

    async def event_generator():
        connected_event = {
            "event_type": "connected",
            "data": {
                "client_id": client_id,
                "category": category,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        }
        yield f"event: connected\ndata: {json.dumps(connected_event['data'])}\nid: init\n\n"

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        conn.queue.get(),
                        timeout=settings.sse_heartbeat_interval,
                    )
                    event_id = f"{int(datetime.now(UTC).timestamp())}-{conn.events_sent_count}"
                    yield f"event: {event['event_type']}\ndata: {json.dumps(event['data'])}\nid: {event_id}\n\n"
                except TimeoutError:
                    heartbeat_data = {"timestamp": datetime.now(UTC).isoformat()}
                    yield f"event: heartbeat\ndata: {json.dumps(heartbeat_data)}\n\n"
                except KeyError:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            await sse_service.disconnect(client_id, db)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/status")
async def sse_status(
    user: dict = Depends(get_optional_token),
):
    if user is None:
        return SuccessResponse(
            data={
                "active_channels": [],
                "connection_id": None,
                "connected_since": None,
            }
        )

    user_id = user.get("user_id", "anonymous")
    tenant_id = user.get("tenant_id", "default")

    active_channels = []
    connected_since = None
    prefix = f"{tenant_id}:{user_id}"
    for client_id, conn in event_router._connections.items():
        if client_id.startswith(prefix) and conn.is_active:
            active_channels.extend(conn.categories)
            if connected_since is None:
                connected_since = conn.connected_at.isoformat()

    return SuccessResponse(
        data={
            "active_channels": active_channels,
            "connection_id": prefix,
            "connected_since": connected_since,
        }
    )


@router.get("/stats")
async def sse_stats():
    stats = await sse_service.get_sse_stats()
    return SuccessResponse(data=stats)
