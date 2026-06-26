# Async client for the Twenty CRM REST API (/rest/*). Auth = Bearer API key.
# Object plurals: people, companies. Records use composite fields
# (name{firstName,lastName}, emails{primaryEmail,...}, phones{...},
# company domainName{primaryLinkUrl,...}).
#
# Twenty's REST layer wraps responses GraphQL-style, e.g.
#   create -> {"data": {"createPerson": {...}}}
#   list   -> {"data": {"people": [...]}}
# _extract_record / _extract_list below tolerate both wrapped and flat shapes.
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.structured_log import log_event

logger = logging.getLogger(__name__)


class TwentyClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # --- response helpers ---

    @staticmethod
    def _extract_record(data: dict[str, Any], verbs_object: str) -> dict[str, Any] | None:
        # verbs_object e.g. "createPerson" / "updatePerson" / "person".
        body = data.get("data", data)
        if isinstance(body, dict):
            if verbs_object in body and isinstance(body[verbs_object], dict):
                return body[verbs_object]
            if "id" in body:
                return body
        return None

    @staticmethod
    def _extract_list(data: dict[str, Any], plural: str) -> list[dict[str, Any]]:
        body = data.get("data", data)
        if isinstance(body, dict) and isinstance(body.get(plural), list):
            return body[plural]
        if isinstance(body, list):
            return body
        return []

    # --- people ---

    async def find_person_id_by_email(self, email: str) -> str | None:
        # Best-effort dedup lookup; the bridge's own mapping table is the primary key.
        if not email:
            return None
        try:
            resp = await self._client.get(
                "/rest/people",
                params={"filter": f"emails.primaryEmail[eq]:{email}", "limit": 1},
            )
            resp.raise_for_status()
            people = self._extract_list(resp.json(), "people")
            return people[0]["id"] if people else None
        except httpx.HTTPError as exc:  # tolerate transport/HTTP errors; let real bugs propagate
            log_event(logger, "twenty_lookup_failed", "person lookup failed",
                      level=logging.WARNING, email_present=bool(email), error=str(exc))
            return None

    async def create_person(self, payload: dict[str, Any]) -> str:
        resp = await self._client.post("/rest/people", json=payload)
        resp.raise_for_status()
        rec = self._extract_record(resp.json(), "createPerson")
        if not rec or "id" not in rec:
            raise RuntimeError("Twenty createPerson returned no id")
        return rec["id"]

    async def update_person(self, person_id: str, payload: dict[str, Any]) -> str:
        resp = await self._client.patch(f"/rest/people/{person_id}", json=payload)
        resp.raise_for_status()
        return person_id

    async def get_person(self, person_id: str) -> dict[str, Any] | None:
        resp = await self._client.get(f"/rest/people/{person_id}")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._extract_record(resp.json(), "person")

    # --- companies ---

    async def find_company_id_by_name(self, name: str) -> str | None:
        if not name:
            return None
        try:
            resp = await self._client.get(
                "/rest/companies",
                params={"filter": f"name[eq]:{name}", "limit": 1},
            )
            resp.raise_for_status()
            companies = self._extract_list(resp.json(), "companies")
            return companies[0]["id"] if companies else None
        except httpx.HTTPError as exc:  # tolerate transport/HTTP errors; let real bugs propagate
            log_event(logger, "twenty_lookup_failed", "company lookup failed",
                      level=logging.WARNING, error=str(exc))
            return None

    async def create_company(self, name: str) -> str:
        resp = await self._client.post("/rest/companies", json={"name": name})
        resp.raise_for_status()
        rec = self._extract_record(resp.json(), "createCompany")
        if not rec or "id" not in rec:
            raise RuntimeError("Twenty createCompany returned no id")
        return rec["id"]
