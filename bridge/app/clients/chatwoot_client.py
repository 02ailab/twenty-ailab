# Async client for the Chatwoot application API
# (/api/v1/accounts/{account_id}/...). Auth = `api_access_token` header.
from __future__ import annotations

from typing import Any

import httpx


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
