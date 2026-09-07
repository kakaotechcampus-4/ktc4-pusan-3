from fastapi import FastAPI

from app.api.v1.routers import health
from app.core.config import settings

app = FastAPI(title=settings.APP_NAME)

app.include_router(health.router, prefix="/api/v1")
