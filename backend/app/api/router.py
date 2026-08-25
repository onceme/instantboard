from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.auth import router as auth_router
from app.api.v1.categories import router as categories_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.finance import router as finance_router
from app.api.v1.health import router as health_router
from app.api.v1.items import router as items_router
from app.api.v1.sources import router as sources_router
from app.api.v1.sse import router as sse_router
from app.api.v1.tech import router as tech_router

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(health_router, tags=["health"])
v1_router.include_router(auth_router, prefix="/auth", tags=["auth"])
v1_router.include_router(categories_router, prefix="/categories", tags=["categories"])
v1_router.include_router(sources_router, prefix="/sources", tags=["sources"])
v1_router.include_router(finance_router, prefix="/finance", tags=["finance"])
v1_router.include_router(tech_router, prefix="/tech", tags=["tech"])
v1_router.include_router(items_router, prefix="/items", tags=["items"])
v1_router.include_router(dashboard_router, prefix="/dashboard", tags=["dashboard"])
v1_router.include_router(sse_router, prefix="/stream", tags=["sse"])
v1_router.include_router(admin_router, prefix="/admin", tags=["admin"])
