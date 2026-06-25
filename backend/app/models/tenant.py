from sqlalchemy import Boolean, CheckConstraint, Column, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class Tenant(BaseModel):
    __tablename__ = "tenants"

    name = Column(String(100), nullable=False)
    slug = Column(String(50), nullable=False, unique=True)
    plan = Column(
        String(20),
        nullable=False,
        default="free",
        server_default="free",
    )
    settings = Column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    max_users = Column(Integer, nullable=False, default=5, server_default="5")
    max_categories = Column(Integer, nullable=False, default=10, server_default="10")
    max_sources = Column(Integer, nullable=False, default=50, server_default="50")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        CheckConstraint(
            "plan IN ('free', 'pro', 'enterprise')",
            name="chk_tenants_plan",
        ),
    )

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan", lazy="noload")
    categories = relationship("Category", back_populates="tenant", cascade="all, delete-orphan", lazy="noload")
    sources = relationship("Source", back_populates="tenant", cascade="all, delete-orphan", lazy="noload")
