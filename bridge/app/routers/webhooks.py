# Inbound webhook endpoints. Both senders are in-cluster (private URLs), so these
# are reached over cluster DNS, not the public ingress (which only exposes /panel).
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response

from app.config import get_settings
from app.security import verify_chatwoot_signature, verify_twenty_signature
from app.services import sync
from app.structured_log import log_event

logger = logging.getLogger(__name__)
router = APIRouter()

_SYNCED_EVENTS = {"contact_created", "contact_updated", "conversation_created"}


@router.post("/webhooks/chatwoot")
async def chatwoot_webhook(request: Request, background: BackgroundTasks) -> Response:
    settings = get_settings()
    raw = await request.body()

    signature = request.headers.get("X-Chatwoot-Signature", "")
    timestamp = request.headers.get("X-Chatwoot-Timestamp", "")
    # If a secret is configured, REQUIRE a valid signature — a missing header must
    # not silently bypass authentication.
    if settings.chatwoot_webhook_secret:
        if not signature or not verify_chatwoot_signature(
            settings.chatwoot_webhook_secret, timestamp, raw, signature
        ):
            log_event(logger, "webhook_signature_invalid", "chatwoot signature missing or mismatch",
                      level=logging.WARNING)
            return Response(status_code=401)

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return Response(status_code=400)

    event = body.get("event", "")
    if event in _SYNCED_EVENTS:
        # Process after responding so Chatwoot's ~5s webhook timeout isn't hit.
        background.add_task(_safe_sync, event, body)
    return Response(status_code=200, content='{"ok":true}', media_type="application/json")


async def _safe_sync(event: str, body: dict) -> None:
    try:
        await sync.sync_contact_to_twenty(event, body)
    except Exception as exc:  # noqa: BLE001 — webhook side-effect must not crash the loop
        log_event(logger, "sync_failed", "contact sync failed",
                  level=logging.ERROR, chatwoot_event=event, error=str(exc))


@router.post("/webhooks/twenty")
async def twenty_webhook(request: Request) -> Response:
    # Direction B (Twenty -> Chatwoot) is a later iteration; for now log & ack.
    settings = get_settings()
    raw = await request.body()
    signature = request.headers.get("X-Twenty-Webhook-Signature", "")
    timestamp = request.headers.get("X-Twenty-Webhook-Timestamp", "")
    if settings.twenty_webhook_secret:
        if not signature or not verify_twenty_signature(
            settings.twenty_webhook_secret, timestamp, raw, signature
        ):
            return Response(status_code=401)
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return Response(status_code=400)
    log_event(logger, "twenty_webhook_received", "twenty webhook (no-op for now)",
              event_name=body.get("eventName"))
    return Response(status_code=200, content='{"ok":true}', media_type="application/json")
