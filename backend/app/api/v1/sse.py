import asyncio
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.constants import SYSTEM_TENANT_ID
from app.core.exceptions import InvalidToken, ValidationError
from app.core.security import extract_user_from_token
from app.core.sse_router import event_router
from app.dependencies import get_db, get_optional_token
from app.schemas.base import SuccessResponse
from app.services.sse import SSEService

router = APIRouter()
sse_service = SSEService()

# Channels aggregated by the literal "all" stream; mirrors the Redis listener's
# subscription set (sse_router.start_redis_listener) minus the pseudo-channel itself.
ALL_CHANNELS = ("finance", "tech", "dashboard", "admin")

# Channel names accepted by the `channels` query parameter on /stream/all —
# the same four channels the literal "all" aggregate covers (api.md §3.8).
VALID_CHANNELS = {"finance", "tech", "dashboard", "admin"}


def _parse_channels(raw: str) -> list[str]:
    """Parse `channels=finance,tech` into a sorted, de-duplicated channel list."""
    requested = [part.strip() for part in raw.split(",") if part.strip()]
    if not requested:
        raise ValidationError(
            message="channels parameter must not be empty",
            details=[{"field": "channels", "message": "must list at least one channel"}],
        )
    unknown = sorted({part for part in requested if part not in VALID_CHANNELS})
    if unknown:
        raise ValidationError(
            message="Unknown channels requested",
            details=[{"field": "channels", "message": f"unknown channel: {name}"} for name in unknown],
        )
    return sorted(set(requested))


async def collect_replay_events(
    category: str,
    last_event_id: str,
    tenant_id: str,
    channels: list[str] | None = None,
) -> list[dict]:
    """Gather the events the client missed while disconnected.

    For a single channel this is the per-channel history after the anchor; for
    the "all" aggregate the histories of the selected channels (defaulting to
    all four) are merged and re-ordered by published_at. Errors resolve to an
    empty list upstream (SSEEventRouter.get_missed_events), so a missing buffer
    or anchor simply yields no replay frames.
    """
    if category == "all":
        missed: list[dict] = []
        for channel in channels or ALL_CHANNELS:
            missed.extend(await event_router.get_missed_events(channel, last_event_id, tenant_id))
        missed.sort(key=lambda event: event.get("published_at") or "")
        return missed
    return await event_router.get_missed_events(category, last_event_id, tenant_id)


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
    # Fallback must be str(SYSTEM_TENANT_ID), never a literal like "default": SSE routing
    # forwards events only on exact tenant_id string match, so a non-UUID literal would
    # isolate this connection from every published event (data-flow.md §3.5.4).
    tenant_id = user.get("tenant_id", str(SYSTEM_TENANT_ID))

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


@router.get("/{category}")
async def sse_stream(
    category: str,
    token: str = Query(..., description="JWT token for SSE authentication"),
    channels: str | None = Query(
        default=None,
        description="Comma-separated channel subset for the 'all' stream (ignored otherwise)",
    ),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: AsyncSession = Depends(get_db),
):
    user_info = extract_user_from_token(token)
    if user_info is None:
        raise InvalidToken()

    user_id = user_info.get("user_id", "anonymous")
    # Same rationale as sse_status: fall back to str(SYSTEM_TENANT_ID), never a literal
    # like "default", or exact-string tenant routing would deliver no events.
    tenant_id = user_info.get("tenant_id", str(SYSTEM_TENANT_ID))
    client_id = f"{tenant_id}:{user_id}:{uuid.uuid4()}"

    # Backward compatibility (api.md §3.8): only the literal "all" stream honours the
    # `channels` selector; single-category paths ignore it and keep their one-segment
    # subscription. Without the selector, "all" keeps the pseudo-channel aggregate.
    if category == "all" and channels is not None:
        selected_channels: list[str] | None = _parse_channels(channels)
    else:
        selected_channels = None

    await sse_service.connect(
        client_id=client_id,
        categories=selected_channels if selected_channels is not None else [category],
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

        # Replay events missed during the disconnect (EventSource auto-reconnect
        # sends the Last-Event-ID header). Runs before entering the live loop so
        # no live event can overtake a replayed one.
        if last_event_id:
            for event in await collect_replay_events(category, last_event_id, tenant_id, selected_channels):
                yield (
                    f"event: {event.get('event_type')}\n"
                    f"data: {json.dumps(event.get('data', {}))}\n"
                    f"id: {event.get('event_id', '')}\n\n"
                )

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
