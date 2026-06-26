# twenty-bridge FastAPI entrypoint. Wires logging, config, DB pool, the shared
# Twenty/Chatwoot clients, and the routers.
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db, deps
from app.clients.chatwoot_client import ChatwootClient
from app.clients.twenty_client import TwentyClient
from app.config import get_settings
from app.logging_setup import setup_logging
from app.routers import health, panel, webhooks
from app.structured_log import log_event

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_format)

    await db.init_pool(settings.postgres_dsn)
    deps.twenty = TwentyClient(settings.twenty_base_url, settings.twenty_api_key,
                               settings.http_timeout_seconds)
    deps.chatwoot = ChatwootClient(settings.chatwoot_base_url, settings.chatwoot_account_id,
                                   settings.chatwoot_api_token, settings.http_timeout_seconds)

    log_event(logger, "bridge_start", "twenty-bridge started",
              chatwoot_base_url=settings.chatwoot_base_url,
              twenty_base_url=settings.twenty_base_url,
              account_id=settings.chatwoot_account_id)
    yield

    log_event(logger, "bridge_stop", "twenty-bridge stopping")
    if deps.twenty:
        await deps.twenty.aclose()
    if deps.chatwoot:
        await deps.chatwoot.aclose()
    await db.close_pool()


app = FastAPI(title="twenty-bridge", lifespan=lifespan)
app.include_router(health.router)
app.include_router(webhooks.router)
app.include_router(panel.router)
