# Chatwoot -> Twenty sync (iteration 1, one direction).
# A Chatwoot contact becomes a Twenty Person, optionally linked to a Twenty
# Company derived from additional_attributes.company_name. Idempotency is driven
# by the bridge's own mapping table; a Twenty email lookup is a best-effort
# fallback so we don't create duplicates the first time we see a contact.
from __future__ import annotations

import logging
from typing import Any

from app import db, deps
from app.config import get_settings
from app.structured_log import log_event

logger = logging.getLogger(__name__)

# Label shown on the clickable Chatwoot link on the Twenty Person card.
CHATWOOT_LINK_LABEL = "Открыть чат"


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


def _person_link_url(person: dict[str, Any] | None, field: str) -> str:
    # Read the current primaryLinkUrl of a Twenty "Links" composite field, if any.
    if not person:
        return ""
    val = person.get(field)
    return val.get("primaryLinkUrl") or "" if isinstance(val, dict) else ""


def _core_matches(person: dict[str, Any], desired: dict[str, Any]) -> bool:
    # True when the fields the bridge manages already equal the desired values, so
    # we can skip the PATCH. Compare ONLY the sub-fields we set (Twenty returns many
    # more), and only those present in `desired` — avoids needless writes that would
    # otherwise echo back as a Twenty webhook once direction B is live.
    p_name = person.get("name") or {}
    d_name = desired.get("name") or {}
    if (p_name.get("firstName") or "") != (d_name.get("firstName") or ""):
        return False
    if (p_name.get("lastName") or "") != (d_name.get("lastName") or ""):
        return False
    if "emails" in desired:
        if ((person.get("emails") or {}).get("primaryEmail") or "") != desired["emails"]["primaryEmail"]:
            return False
    if "phones" in desired:
        if ((person.get("phones") or {}).get("primaryPhoneNumber") or "") != desired["phones"]["primaryPhoneNumber"]:
            return False
    if "companyId" in desired:
        if (person.get("companyId") or None) != desired["companyId"]:
            return False
    return True


def _chatwoot_link_url(chatwoot_contact_id: int, conversation_display_id: int | None) -> str:
    # Prefer a deep link to the contact's latest conversation; fall back to the
    # contact page when the contact has no conversation yet.
    settings = get_settings()
    base = settings.chatwoot_public_url.rstrip("/")
    acct = settings.chatwoot_account_id
    if conversation_display_id is not None:
        return f"{base}/app/accounts/{acct}/conversations/{conversation_display_id}"
    return f"{base}/app/accounts/{acct}/contacts/{chatwoot_contact_id}"


async def sync_contact_to_twenty(event: str, payload: dict[str, Any]) -> str | None:
    contact = extract_contact(event, payload)
    if not contact:
        log_event(logger, "sync_skipped", "no contact in event",
                  level=logging.WARNING, chatwoot_event=event)
        return None

    chatwoot_contact_id = int(contact["id"])
    twenty = deps.require_twenty()
    chatwoot = deps.require_chatwoot()
    settings = get_settings()

    additional = contact.get("additional_attributes") or {}
    company_id = await _upsert_company(additional.get("company_name") or "")

    # Resolve the Twenty person: own mapping first, else best-effort email dedup.
    mapped_id = await db.get_twenty_person_id(chatwoot_contact_id)
    if not mapped_id:
        email = contact.get("email") or ""
        mapped_id = await twenty.find_person_id_by_email(email) if email else None

    person: dict[str, Any] | None = None
    if mapped_id:
        desired = build_person_payload(contact, company_id, False)
        person = await twenty.get_person(mapped_id)
        # Skip the write when nothing the bridge manages changed (anti-echo). If the
        # record can't be read, fall back to the old unconditional update so a stale
        # mapping still surfaces as a 404 rather than silently diverging.
        if person is None or not _core_matches(person, desired):
            await twenty.update_person(mapped_id, desired)
        person_id = mapped_id
        action = "updated"
    else:
        person_id = await twenty.create_person(build_person_payload(contact, company_id, True))
        action = "created"
    await db.set_twenty_person_id(chatwoot_contact_id, person_id)

    # Put a clickable Chatwoot link on the Twenty card → the contact's most recent
    # conversation (fallback: the contact page). Best-effort; skip if already set to
    # the same URL to avoid a needless write / Twenty-webhook echo.
    display_id = await chatwoot.get_latest_conversation_display_id(chatwoot_contact_id)
    link_url = _chatwoot_link_url(chatwoot_contact_id, display_id)
    if _person_link_url(person, settings.twenty_chatwoot_field) != link_url:
        await twenty.set_person_link(person_id, settings.twenty_chatwoot_field,
                                     link_url, CHATWOOT_LINK_LABEL)

    # Write the Twenty id back onto the Chatwoot contact (merge, skip if unchanged).
    await chatwoot.set_twenty_id(chatwoot_contact_id, person_id, additional)

    log_event(logger, "contact_synced", "contact synced to Twenty",
              chatwoot_event=event, chatwoot_contact_id=chatwoot_contact_id,
              twenty_person_id=person_id, action=action,
              company_linked=bool(company_id))
    return person_id


# --- direction B: Twenty Person -> Chatwoot contact (write-back) ---

def _person_full_name(record: dict[str, Any]) -> str:
    name = record.get("name") or {}
    first = (name.get("firstName") or "").strip()
    last = (name.get("lastName") or "").strip()
    return f"{first} {last}".strip()


async def sync_person_to_chatwoot(record: dict[str, Any]) -> int | None:
    # Mirror of sync_contact_to_twenty for the reverse direction. Only touches the
    # native Chatwoot identity fields (name/email/phone) — never pushes rich Twenty
    # data, which must stay in the admin-only panel. The actual anti-clobber /
    # anti-echo skip lives in ChatwootClient.update_identity_fields.
    person_id = record.get("id")
    if not person_id:
        return None

    chatwoot_contact_id = await db.get_chatwoot_contact_id(str(person_id))
    if not chatwoot_contact_id:
        # Person not managed by the bridge (e.g. created directly in Twenty) — ignore.
        log_event(logger, "reverse_sync_skipped", "twenty person not mapped to chatwoot",
                  twenty_person_id=str(person_id))
        return None

    name = _person_full_name(record)
    email = (record.get("emails") or {}).get("primaryEmail") or ""
    phone = (record.get("phones") or {}).get("primaryPhoneNumber") or ""

    wrote = await deps.require_chatwoot().update_identity_fields(
        chatwoot_contact_id,
        name=name or None,
        email=email or None,
        phone=phone or None,
    )
    log_event(logger, "person_synced_to_chatwoot", "twenty person synced to Chatwoot",
              twenty_person_id=str(person_id), chatwoot_contact_id=chatwoot_contact_id,
              wrote=wrote)
    return chatwoot_contact_id
