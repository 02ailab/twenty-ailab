# Async client for the Chatwoot application API
# (/api/v1/accounts/{account_id}/...). Auth = `api_access_token` header.
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.structured_log import log_event

logger = logging.getLogger(__name__)


class ChatwootClient:
    def __init__(self, base_url: str, account_id: int, api_token: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._account_id = account_id
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={"api_access_token": api_token, "Content-Type": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _contacts(self) -> str:
        return f"/api/v1/accounts/{self._account_id}/contacts"

    async def get_contact(self, contact_id: int) -> dict[str, Any] | None:
        resp = await self._client.get(f"{self._contacts()}/{contact_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        body = resp.json()
        # Chatwoot wraps single contact under payload (sometimes payload.contact).
        payload = body.get("payload", body)
        if isinstance(payload, dict) and "contact" in payload:
            return payload["contact"]
        return payload

    async def update_contact(self, contact_id: int, patch: dict[str, Any]) -> dict[str, Any]:
        resp = await self._client.patch(f"{self._contacts()}/{contact_id}", json=patch)
        resp.raise_for_status()
        return resp.json()

    async def get_latest_conversation_display_id(self, contact_id: int) -> int | None:
        # The contact's conversations come back ordered by last_activity_at desc, so
        # payload[0] is the most recently active — "the chat with this client". Each
        # item's `id` is the conversation display_id (the value the dashboard URL uses).
        # Best-effort: any error / no conversations -> None (caller falls back to the
        # contact page).
        try:
            resp = await self._client.get(f"{self._contacts()}/{contact_id}/conversations")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            log_event(logger, "chatwoot_conversations_lookup_failed",
                      "could not list contact conversations",
                      level=logging.WARNING, contact_id=contact_id, error=str(exc))
            return None
        body = resp.json()
        # Tolerate {"payload": [...]}, {"data": {"payload": [...]}} and a bare list.
        items = body.get("payload", body) if isinstance(body, dict) else body
        if isinstance(items, dict):
            items = items.get("payload", [])
        if not isinstance(items, list) or not items:
            return None
        first = items[0]
        display_id = first.get("id") if isinstance(first, dict) else None
        return int(display_id) if display_id is not None else None

    async def get_conversation_messages(self, conversation_display_id: int,
                                        max_messages: int = 1000) -> list[dict[str, Any]]:
        # Fetch a conversation's FULL message history to build the Twenty Note
        # transcript. The index endpoint returns only the latest page (~20) under
        # {payload: [...]} ascending; older pages are fetched with ?before=<oldest id>.
        # We page backwards (prepending older pages) until exhausted, a page makes no
        # progress, or the hard caps below — so the note is the whole transcript, not
        # just the tail. Best-effort: a first-page error -> [] (caller skips the note);
        # a later-page error -> the partial transcript collected so far.
        url = f"/api/v1/accounts/{self._account_id}/conversations/{conversation_display_id}/messages"
        collected: list[dict[str, Any]] = []
        before: int | None = None
        oldest_seen: int | None = None
        for _ in range(50):  # page cap: 50 * ~20 ≈ 1000 messages
            try:
                resp = await self._client.get(url, params={"before": before} if before is not None else None)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log_event(logger, "chatwoot_messages_lookup_failed",
                          "could not list conversation messages",
                          level=logging.WARNING, conversation_display_id=conversation_display_id,
                          error=str(exc))
                break
            body = resp.json()
            items = body.get("payload", body) if isinstance(body, dict) else body
            if not isinstance(items, list) or not items:
                break
            collected = items + collected
            ids = [m.get("id") for m in items if isinstance(m, dict) and isinstance(m.get("id"), int)]
            if not ids:
                break
            oldest = min(ids)
            # Stop if the cursor didn't advance (defensive against an API that ignores
            # `before` — otherwise we'd loop on the same page).
            if oldest_seen is not None and oldest >= oldest_seen:
                break
            oldest_seen = oldest
            before = oldest
            if len(collected) >= max_messages:
                break
        return collected

    async def update_identity_fields(self, contact_id: int, *, name: str | None = None,
                                     email: str | None = None, phone: str | None = None) -> bool:
        # Direction B write-back: push Twenty's basic identity onto the native
        # Chatwoot contact. Read-compare-write so an unchanged value produces NO
        # PATCH — this is what stops the Chatwoot<->Twenty echo loop. A None field
        # means "don't touch" (never clobber Chatwoot with an empty value). Returns
        # True only if something was actually written.
        current = await self.get_contact(contact_id)
        if current is None:
            return False
        patch: dict[str, Any] = {}
        if name and (current.get("name") or "") != name:
            patch["name"] = name
        if email and (current.get("email") or "") != email:
            patch["email"] = email
        if phone and (current.get("phone_number") or "") != phone:
            patch["phone_number"] = phone
        if not patch:
            return False
        await self.update_contact(contact_id, patch)
        return True

    async def set_twenty_id(self, contact_id: int, twenty_id: str,
                            current_additional: dict[str, Any] | None) -> None:
        # Merge to avoid clobbering other additional_attributes keys. Chatwoot can
        # return non-dict shapes here, so coerce defensively instead of dict()-ing a
        # string/list (which would raise).
        additional = dict(current_additional) if isinstance(current_additional, dict) else {}
        existing_external = additional.get("external")
        external = dict(existing_external) if isinstance(existing_external, dict) else {}
        if external.get("twenty_id") == twenty_id:
            return  # already linked — no write, avoids a needless contact_updated echo
        external["twenty_id"] = twenty_id
        additional["external"] = external
        await self.update_contact(contact_id, {"additional_attributes": additional})
