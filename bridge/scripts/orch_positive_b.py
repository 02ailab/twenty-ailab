# Positive-chain probe (work order twenty-ailab P2-8) for orchestration-check --positive.
#
# Proves the happy path Chatwoot -> bridge -> Twenty end to end, safely:
#   1. create a SYNTHETIC Chatwoot contact (test-orch-*, free-domain email so NO Company
#      is ever created -> deterministic cleanup);
#   2. fire a contact_updated webhook signed with the REAL CHATWOOT_WEBHOOK_SECRET at the
#      bridge -> expect 200;
#   3. confirm a Twenty Person + bridge contact_map row appear (background sync);
#   4. fire the SAME webhook again -> expect 200 and the SAME person_id (idempotency);
#   5. ALWAYS clean up: delete the Twenty Person, the bridge mapping rows, and the
#      synthetic Chatwoot contact (idempotent; tolerates 404).
#
# Designed to run INSIDE the twenty-bridge-api pod (kubectl exec -i ... -- python - < this),
# where app deps (httpx, asyncpg) and all secrets (env via envFrom) are present. The HMAC
# secret is read from the pod env and used to sign in-process — it never leaves the pod and
# is never printed. The ONLY thing emitted on stdout is a single JSON result line. The HMAC
# scheme and webhook payload shape are NOT changed here — this is a test harness around the
# existing contract.
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sys
import time

import asyncpg
import httpx

from app.config import get_settings

# Synthetic contact id range is irrelevant (Chatwoot assigns the real id); the email marker
# keeps it identifiable and the free domain guarantees no Company is spawned.
MARKER = "test-orch"


def _log(msg: str) -> None:
    # Progress goes to stderr so stdout carries only the final JSON result.
    print(f"orch-positive-b: {msg}", file=sys.stderr, flush=True)


def _sign_chatwoot(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"sha256={mac.hexdigest()}"


def _unwrap_contact(body: dict) -> dict | None:
    payload = body.get("payload", body) if isinstance(body, dict) else body
    if isinstance(payload, dict) and "contact" in payload:
        return payload["contact"]
    return payload if isinstance(payload, dict) else None


async def _wait_for_mapping(pool: asyncpg.Pool, contact_id: int, timeout_s: float = 20.0) -> str | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        row = await pool.fetchval(
            "SELECT twenty_person_id FROM contact_map WHERE chatwoot_contact_id = $1", contact_id
        )
        if row:
            return str(row)
        await asyncio.sleep(0.5)
    return None


async def run() -> int:
    s = get_settings()
    run_id = int(time.time())
    cw = httpx.AsyncClient(
        base_url=s.chatwoot_base_url.rstrip("/"), timeout=s.http_timeout_seconds,
        headers={"api_access_token": s.chatwoot_api_token, "Content-Type": "application/json"},
    )
    tw = httpx.AsyncClient(
        base_url=s.twenty_base_url.rstrip("/"), timeout=s.http_timeout_seconds,
        headers={"Authorization": f"Bearer {s.twenty_api_key}", "Content-Type": "application/json"},
    )
    bridge = httpx.AsyncClient(base_url="http://localhost:8000", timeout=s.http_timeout_seconds)
    pool = await asyncpg.create_pool(s.postgres_dsn, min_size=1, max_size=2)

    contact_id: int | None = None
    person_id: str | None = None
    result: dict = {"ok": False, "stage": "init"}
    acct = s.chatwoot_account_id
    try:
        # 1. synthetic Chatwoot contact (free-domain email -> no Company)
        result["stage"] = "create_contact"
        email = f"{MARKER}-{run_id}@gmail.com"
        name = f"{MARKER} {run_id}"
        resp = await cw.post(f"/api/v1/accounts/{acct}/contacts",
                             json={"name": name, "email": email,
                                   "additional_attributes": {"orch_test": True}})
        resp.raise_for_status()
        contact = _unwrap_contact(resp.json())
        if not contact or contact.get("id") is None:
            raise RuntimeError("Chatwoot did not return a contact id")
        contact_id = int(contact["id"])
        _log(f"created synthetic Chatwoot contact id={contact_id}")

        # 2+4. fire the signed webhook twice; expect 200 each time
        webhook_body = {
            "event": "contact_updated", "id": contact_id, "name": name,
            "email": email, "phone_number": "",
            "additional_attributes": {"orch_test": True},
        }
        raw = json.dumps(webhook_body, separators=(",", ":")).encode()

        async def fire(label: str) -> None:
            ts = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "X-Chatwoot-Timestamp": ts,
                "X-Chatwoot-Signature": _sign_chatwoot(s.chatwoot_webhook_secret, ts, raw),
            }
            r = await bridge.post("/webhooks/chatwoot", content=raw, headers=headers)
            if r.status_code != 200:
                raise RuntimeError(f"{label}: bridge returned {r.status_code} (expected 200)")
            _log(f"{label}: webhook accepted (200)")

        result["stage"] = "fire_1"
        await fire("fire#1")
        person_id = await _wait_for_mapping(pool, contact_id)
        if not person_id:
            raise RuntimeError("no contact_map row appeared after fire#1 (sync did not complete)")
        _log(f"mapping created -> twenty_person_id={person_id}")

        # confirm the Person actually exists in Twenty
        result["stage"] = "verify_person"
        pr = await tw.get(f"/rest/people/{person_id}")
        if pr.status_code != 200:
            raise RuntimeError(f"Twenty person {person_id} not found (status {pr.status_code})")

        # idempotency: same webhook -> same person, no duplicate
        result["stage"] = "fire_2"
        await fire("fire#2")
        person_id_2 = await _wait_for_mapping(pool, contact_id)
        if person_id_2 != person_id:
            raise RuntimeError(f"idempotency broken: {person_id} != {person_id_2}")
        _log("idempotent: second webhook resolved to the same person")

        result = {"ok": True, "stage": "done", "contact_id": contact_id,
                  "person_id": person_id, "idempotent": True}
        return 0
    except Exception as exc:  # noqa: BLE001 — report cleanly, then always clean up
        result = {"ok": False, "stage": result.get("stage"), "error": str(exc),
                  "contact_id": contact_id, "person_id": person_id}
        return 1
    finally:
        # 5. idempotent cleanup — runs no matter what, touching ONLY the synthetic ids.
        try:
            if person_id:
                dr = await tw.request("DELETE", f"/rest/people/{person_id}")
                _log(f"cleanup: deleted Twenty person {person_id} (status {dr.status_code})")
        except Exception as exc:  # noqa: BLE001
            _log(f"cleanup WARN: Twenty person delete failed: {exc}")
        try:
            if contact_id is not None:
                # mapping rows are keyed by the synthetic ids; no Company was created.
                await pool.execute("DELETE FROM contact_map WHERE chatwoot_contact_id = $1", contact_id)
                _log(f"cleanup: deleted contact_map row for {contact_id}")
        except Exception as exc:  # noqa: BLE001
            _log(f"cleanup WARN: contact_map delete failed: {exc}")
        try:
            if contact_id is not None:
                cr = await cw.request("DELETE", f"/api/v1/accounts/{acct}/contacts/{contact_id}")
                _log(f"cleanup: deleted synthetic Chatwoot contact {contact_id} (status {cr.status_code})")
        except Exception as exc:  # noqa: BLE001
            _log(f"cleanup WARN: Chatwoot contact delete failed: {exc}")
        await pool.close()
        await cw.aclose()
        await tw.aclose()
        await bridge.aclose()
        # single machine-readable result line on stdout
        print(json.dumps(result))


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
