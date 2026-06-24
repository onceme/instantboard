import uuid
from datetime import UTC, datetime

from sqlalchemy import UUID, Column, DateTime, func, text
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        server_default=text("NOW()"),
        onupdate=func.now(),
    )


class TenantMixin:
    tenant_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )


class BaseModel(Base, TimestampMixin):
    __abstract__ = True

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
