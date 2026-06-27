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
# Conversation resolve arrives as conversation_status_changed (status=resolved);
# it drives the Twenty Note transcript, not the contact upsert.
_NOTE_EVENTS = {"conversation_status_changed"}


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
    # Process after responding so Chatwoot's ~5s webhook timeout isn't hit.
    if event in _SYNCED_EVENTS:
        background.add_task(_safe_sync, event, body)
    elif event in _NOTE_EVENTS:
        background.add_task(_safe_note_sync, body)
    return Response(status_code=200, content='{"ok":true}', media_type="application/json")


async def _safe_sync(event: str, body: dict) -> None:
    try:
        await sync.sync_contact_to_twenty(event, body)
    except Exception as exc:  # noqa: BLE001 — webhook side-effect must not crash the loop
        log_event(logger, "sync_failed", "contact sync failed",
                  level=logging.ERROR, chatwoot_event=event, error=str(exc))


async def _safe_note_sync(body: dict) -> None:
    try:
        await sync.sync_conversation_to_note(body)
    except Exception as exc:  # noqa: BLE001 — webhook side-effect must not crash the loop
        log_event(logger, "note_sync_failed", "conversation->note sync failed",
                  level=logging.ERROR, error=str(exc))


_TWENTY_SYNCED_EVENTS = {"person.created", "person.updated"}


@router.post("/webhooks/twenty")
async def twenty_webhook(request: Request, background: BackgroundTasks) -> Response:
    # Direction B: a Twenty Person change writes the basic identity back onto the
    # mapped Chatwoot contact. Ack fast (Twenty's webhook timeout is 5s) and process
    # in the background, same as the Chatwoot handler.
    settings = get_settings()
    raw = await request.body()

    signature = request.headers.get("X-Twenty-Webhook-Signature", "")
    timestamp = request.headers.get("X-Twenty-Webhook-Timestamp", "")
    # This endpoint is publicly reachable (Twenty's SSRF guard blocks the in-cluster
    # address, so Twenty posts to the public ingress). Fail closed: with no secret we
    # cannot authenticate a public caller, so refuse rather than accept unsigned.
    if not settings.twenty_webhook_secret:
        log_event(logger, "twenty_webhook_unconfigured",
                  "TWENTY_WEBHOOK_SECRET not set — refusing public webhook",
                  level=logging.ERROR)
        return Response(status_code=401)
    if not signature or not verify_twenty_signature(
        settings.twenty_webhook_secret, timestamp, raw, signature
    ):
        log_event(logger, "webhook_signature_invalid", "twenty signature missing or mismatch",
                  level=logging.WARNING)
        return Response(status_code=401)

    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return Response(status_code=400)

    event = body.get("eventName", "")
    if event in _TWENTY_SYNCED_EVENTS:
        record = body.get("record") or {}
        background.add_task(_safe_reverse_sync, event, record)
    return Response(status_code=200, content='{"ok":true}', media_type="application/json")


async def _safe_reverse_sync(event: str, record: dict) -> None:
    try:
        await sync.sync_person_to_chatwoot(record)
    except Exception as exc:  # noqa: BLE001 — webhook side-effect must not crash the loop
        log_event(logger, "reverse_sync_failed", "twenty->chatwoot sync failed",
                  level=logging.ERROR, twenty_event=event, error=str(exc))
