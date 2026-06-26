# Chatwoot -> Twenty sync (iteration 1, one direction).
# A Chatwoot contact becomes a Twenty Person, optionally linked to a Twenty
# Company derived from additional_attributes.company_name. Idempotency is driven
# by the bridge's own mapping table; a Twenty email lookup is a best-effort
# fallback so we don't create duplicates the first time we see a contact.
from __future__ import annotations

import logging
from typing import Any

from app import db, deps
from app.structured_log import log_event

logger = logging.getLogger(__name__)


def split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "").strip().split(maxsplit=1)
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[1]


def normalize_company_key(name: str) -> str:
    return (name or "").strip().lower()


def extract_contact(event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    # contact_created/contact_updated -> the payload itself is the contact.
    # conversation_created -> contact is under meta.sender (or contact_inbox.contact).
    if event in ("contact_created", "contact_updated"):
        return payload if payload.get("id") is not None else None
    if event == "conversation_created":
        meta = payload.get("meta") or {}
        sender = meta.get("sender")
        if sender and sender.get("id") is not None:
            return sender
        ci = payload.get("contact_inbox") or {}
        contact = ci.get("contact")
        # Only use the fallback contact if it carries an id; otherwise skip cleanly
        # (sync_skipped) instead of crashing later on int(contact["id"]).
        if contact and contact.get("id") is not None:
            return contact
        return None
    return None


def build_person_payload(contact: dict[str, Any], company_id: str | None,
                         include_created_by: bool) -> dict[str, Any]:
    first, last = split_name(contact.get("name") or "")
    email = contact.get("email") or ""
    phone = contact.get("phone_number") or ""

    payload: dict[str, Any] = {
        "name": {"firstName": first, "lastName": last},
    }
    if email:
        payload["emails"] = {"primaryEmail": email, "additionalEmails": []}
    if phone:
        payload["phones"] = {
            "primaryPhoneNumber": phone,
            "primaryPhoneCountryCode": "",
            "primaryPhoneCallingCode": "",
        }
    if company_id:
        payload["companyId"] = company_id
    if include_created_by:
        payload["createdBy"] = {"source": "API"}
    return payload


async def _upsert_company(company_name: str) -> str | None:
    if not company_name:
        return None
    twenty = deps.require_twenty()
    key = normalize_company_key(company_name)
    company_id = await db.get_twenty_company_id(key)
    if company_id:
        return company_id
    company_id = await twenty.find_company_id_by_name(company_name)
    if not company_id:
        company_id = await twenty.create_company(company_name)
        log_event(logger, "twenty_company_created", "company created",
                  company_key=key, twenty_company_id=company_id)
    await db.set_twenty_company_id(key, company_id)
    return company_id


async def sync_contact_to_twenty(event: str, payload: dict[str, Any]) -> str | None:
    contact = extract_contact(event, payload)
    if not contact:
        log_event(logger, "sync_skipped", "no contact in event",
                  level=logging.WARNING, chatwoot_event=event)
        return None

    chatwoot_contact_id = int(contact["id"])
    twenty = deps.require_twenty()
    chatwoot = deps.require_chatwoot()

    additional = contact.get("additional_attributes") or {}
    company_id = await _upsert_company(additional.get("company_name") or "")

    mapped_id = await db.get_twenty_person_id(chatwoot_contact_id)
    if mapped_id:
        await twenty.update_person(mapped_id, build_person_payload(contact, company_id, False))
        person_id = mapped_id
        action = "updated"
    else:
        email = contact.get("email") or ""
        found = await twenty.find_person_id_by_email(email) if email else None
        if found:
            await twenty.update_person(found, build_person_payload(contact, company_id, False))
            person_id = found
            action = "linked"
        else:
            person_id = await twenty.create_person(
                build_person_payload(contact, company_id, True)
            )
            action = "created"
        await db.set_twenty_person_id(chatwoot_contact_id, person_id)

    # Write the Twenty id back onto the Chatwoot contact (merge, skip if unchanged).
    await chatwoot.set_twenty_id(chatwoot_contact_id, person_id, additional)

    log_event(logger, "contact_synced", "contact synced to Twenty",
              chatwoot_event=event, chatwoot_contact_id=chatwoot_contact_id,
              twenty_person_id=person_id, action=action,
              company_linked=bool(company_id))
    return person_id
