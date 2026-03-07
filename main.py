import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from config import settings
from database.session import create_tables
from routes.slack import router as slack_router
from routes.webhook import router as webhook_router

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    yield


app = FastAPI(
    title="Pipe-Stream Logistics Engine",
    version="0.1.0",
    description=(
        "Automates broker email → Rate Confirmation + Release Document pipeline "
        "with human-in-the-loop approval via Slack."
    ),
    lifespan=lifespan,
)

app.include_router(webhook_router)
app.include_router(slack_router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@app.get("/health", tags=["meta"])
def health_check() -> dict:
    return {
        "status": "ok",
        "shadow_mode": settings.SHADOW_MODE,
        "env": settings.APP_ENV,
    }
