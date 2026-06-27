# Chatwoot -> Twenty sync (iteration 1, one direction).
# A Chatwoot contact becomes a Twenty Person, optionally linked to a Twenty
# Company. The company name is taken from additional_attributes.company_name when
# present, else derived from a corporate email domain; free-provider emails (gmail
# etc.) yield no company so individuals stay company-less (no fake bucket).
# Idempotency is driven by the bridge's own mapping table; a Twenty email lookup is
# a best-effort fallback so we don't create duplicates the first time we see a contact.
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


# Free email providers — a contact on one of these is an individual, not a company.
# We never group them under a "provider" company; they stay company-less. Lowercase.
FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com",
    "yandex.ru", "yandex.com", "ya.ru",
    "mail.ru", "bk.ru", "inbox.ru", "list.ru", "internet.ru",
    "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "pm.me",
    "gmx.com", "gmx.net", "web.de", "aol.com", "zoho.com",
    "qq.com", "163.com", "126.com",
})

# Multi-label public suffixes special-cased so acme.co.uk -> "acme", not "co".
_MULTI_PART_TLDS = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk",
    "com.br", "com.au", "com.tr", "com.ua", "co.jp", "co.nz", "co.za", "com.mx",
})


def _registrable_label(domain: str) -> str:
    # The company-ish label of a domain: the part left of the effective TLD.
    # acme.com -> "acme"; mail.acme.com -> "acme"; acme.co.uk -> "acme".
    parts = domain.split(".")
    if len(parts) < 2:
        return ""
    if ".".join(parts[-2:]) in _MULTI_PART_TLDS and len(parts) >= 3:
        return parts[-3]
    return parts[-2]


def _company_name_from_email(email: str) -> str | None:
    # Derive a company name from a corporate email domain. Free providers and
    # malformed addresses yield None (the person stays company-less — the
    # "no fake bucket" decision in the company-source design).
    if "@" not in email:
        return None
    domain = email.rsplit("@", 1)[1].strip().lower()
    if not domain or domain in FREE_EMAIL_DOMAINS:
        return None
    label = _registrable_label(domain)
    return label.capitalize() if label else None


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
    # Company name: explicit Chatwoot field first, else corporate email domain.
    # Free-provider / no email -> stays empty -> Person without a company.
    company_name = (additional.get("company_name") or "").strip()
    if not company_name:
        derived = _company_name_from_email(contact.get("email") or "")
        if derived:
            company_name = derived
            log_event(logger, "company_resolved_from_email",
                      "company name derived from email domain",
                      chatwoot_contact_id=chatwoot_contact_id, company=company_name)
    company_id = await _upsert_company(company_name)

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


# --- conversation -> Twenty Note (one note per conversation, refreshed on resolve) ---

NOTE_TITLE_PREFIX = "Переписка Chatwoot"


def _format_message_line(msg: dict[str, Any]) -> str | None:
    # One transcript line per real message. Skip private agent notes and `activity`
    # (message_type 2 = status changes etc.) so internal/system noise never reaches
    # the CRM note. incoming=0 → client, everything else → agent side.
    if msg.get("private"):
        return None
    if msg.get("message_type") == 2:
        return None
    content = (msg.get("content") or "").strip()
    if not content:
        return None
    who = "Клиент" if msg.get("message_type") == 0 else "Агент"
    return f"**{who}:** {content}"


def _build_note_markdown(messages: list[dict[str, Any]], max_messages: int) -> tuple[str, int]:
    lines = [line for msg in messages if (line := _format_message_line(msg))]
    if max_messages > 0 and len(lines) > max_messages:
        lines = lines[-max_messages:]  # keep the most recent within the cap
    return "\n\n".join(lines), len(lines)


async def sync_conversation_to_note(payload: dict[str, Any]) -> str | None:
    # On conversation_status_changed/resolved, write the transcript to a Twenty Note
    # on the contact's Person. One note per conversation (keyed by display_id); on a
    # later resolve (after reopen) the SAME note's body is rewritten with the latest
    # full transcript — no duplicates, never stale.
    settings = get_settings()
    if not settings.note_sync_enabled:
        return None
    if (payload.get("status") or "") != "resolved":
        return None

    display_id = payload.get("id")
    if display_id is None:
        return None
    display_id = int(display_id)

    sender = (payload.get("meta") or {}).get("sender") or {}
    contact_id = sender.get("id")
    person_id = await db.get_twenty_person_id(int(contact_id)) if contact_id is not None else None
    if not person_id:
        log_event(logger, "note_skipped_no_person", "conversation contact not mapped to a person",
                  conversation_display_id=display_id)
        return None

    chatwoot = deps.require_chatwoot()
    twenty = deps.require_twenty()

    messages = await chatwoot.get_conversation_messages(display_id)
    body, count = _build_note_markdown(messages, settings.note_max_messages)
    if count == 0:
        log_event(logger, "note_skipped_empty", "no usable messages in conversation",
                  conversation_display_id=display_id)
        return None

    title = f"{NOTE_TITLE_PREFIX} #{display_id}"
    note_id = await db.get_note_id(display_id)
    if note_id:
        await twenty.update_note(note_id, title, body)
        action = "updated"
    else:
        note_id = await twenty.create_note(title, body)
        await twenty.link_note_to_person(note_id, person_id)
        await db.set_note_id(display_id, note_id)
        action = "created"

    log_event(logger, "conversation_note_created", "conversation synced to Twenty note",
              conversation_display_id=display_id, twenty_note_id=note_id,
              twenty_person_id=person_id, message_count=count, action=action)
    return note_id
