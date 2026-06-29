# HMAC verification for inbound webhooks. Both Chatwoot and Twenty sign with
# HMAC-SHA256 but over slightly different strings (see each function). Since both
# senders are in-cluster (private webhook URLs), verification is defense-in-depth;
# exact schemes were derived from the codebases and should be confirmed against the
# live instances on first real delivery.
from __future__ import annotations

import hashlib
import hmac
import time


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


# --- Panel session token ------------------------------------------------------
# Short-lived token the /panel page mints for itself so the long-lived
# PANEL_SHARED_SECRET stops riding in every /panel/api/contact call (where it
# would leak into Traefik logs / Referer / browser history). It is signed with
# the SAME shared secret (no new secret to manage) but over a distinct domain
# string and carries an expiry, so a captured token is useless after a few
# minutes. This is bridge-internal (page<->API) — NOT a cross-service contract,
# so it is safe to change unilaterally; the Chatwoot-supplied /panel?secret=
# entry URL is untouched. The full Chatwoot-signed session token is a separate,
# operator-gated step (canon §8C.7).

def mint_panel_token(secret: str, ttl_seconds: int, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + ttl_seconds)
    sig = hmac.new(secret.encode(), f"panel.{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_panel_token(secret: str, token: str, now: float | None = None) -> bool:
    if not secret or not token or "." not in token:
        return False
    exp_str, _, sig = token.partition(".")
    if not exp_str.isdigit() or not sig:
        return False
    expected = hmac.new(secret.encode(), f"panel.{exp_str}".encode(), hashlib.sha256).hexdigest()
    if not _const_eq(expected, sig):
        return False
    return int(exp_str) > (now if now is not None else time.time())


def verify_chatwoot_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    # Chatwoot: sha256=HMAC_SHA256(secret, "{timestamp}.{raw_body}"), ts in seconds.
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256)
    expected = f"sha256={mac.hexdigest()}"
    return _const_eq(expected, signature)


def timestamp_fresh_ms(timestamp_ms: str, max_age_seconds: float, now: float | None = None) -> bool:
    # Replay/freshness guard for the public Twenty webhook. The timestamp is part of
    # the SIGNED string ("{ts}:{body}"), so a replayed old-but-valid request still
    # carries its original (stale) timestamp — rejecting outside a window blocks it
    # without weakening the HMAC scheme. Symmetric window absorbs clock skew.
    ts = timestamp_ms.strip()
    if not ts or not ts.lstrip("-").isdigit():
        return False
    now_s = now if now is not None else time.time()
    return abs(now_s - int(ts) / 1000.0) <= max_age_seconds


def verify_twenty_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    # Twenty: signature = HMAC_SHA256(secret, "{timestamp}:{raw_body}") as a BARE hex
    # digest (no "sha256=" prefix), ts in ms. raw_body is exactly JSON.stringify of
    # the payload Twenty signs. Verified against call-webhook.job.ts:28-32.
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode(), f"{timestamp}:".encode() + raw_body, hashlib.sha256)
    return _const_eq(mac.hexdigest(), signature)
