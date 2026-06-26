# HMAC verification for inbound webhooks. Both Chatwoot and Twenty sign with
# HMAC-SHA256 but over slightly different strings (see each function). Since both
# senders are in-cluster (private webhook URLs), verification is defense-in-depth;
# exact schemes were derived from the codebases and should be confirmed against the
# live instances on first real delivery.
from __future__ import annotations

import hashlib
import hmac


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def verify_chatwoot_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    # Chatwoot: sha256=HMAC_SHA256(secret, "{timestamp}.{raw_body}"), ts in seconds.
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    return _const_eq(expected, signature)


def verify_twenty_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    # Twenty: signature = HMAC_SHA256(secret, "{timestamp}:{raw_body}") as a BARE hex
    # digest (no "sha256=" prefix), ts in ms. raw_body is exactly JSON.stringify of
    # the payload Twenty signs. Verified against call-webhook.job.ts:28-32.
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode(), f"{timestamp}:".encode() + raw_body, hashlib.sha256)
    return _const_eq(mac.hexdigest(), signature)
