from datetime import datetime

from pydantic import BaseModel


class SSEConnectionStatus(BaseModel):
    active_channels: list[str]
    connection_id: str
    connected_since: datetime | None = None
