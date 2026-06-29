# Unit tests for the HMAC / panel-token / freshness helpers. Pure functions, no
# network or DB — they pin the security-critical behaviour the audit flagged as
# untested (P0-2 panel token, P1-2 replay window, P0-3 webhook HMAC schemes).
import hashlib
import hmac

from app.security import (
    mint_panel_token,
    timestamp_fresh_ms,
    verify_chatwoot_signature,
    verify_panel_token,
    verify_twenty_signature,
)

SECRET = "s3cr3t-panel"


def test_panel_token_roundtrips_within_ttl():
    token = mint_panel_token(SECRET, ttl_seconds=1800, now=1000.0)
    assert verify_panel_token(SECRET, token, now=1000.0) is True
    assert verify_panel_token(SECRET, token, now=2799.0) is True  # just before exp


def test_panel_token_rejected_after_expiry():
    token = mint_panel_token(SECRET, ttl_seconds=1800, now=1000.0)
    assert verify_panel_token(SECRET, token, now=2800.0) is False  # exp == now -> expired
    assert verify_panel_token(SECRET, token, now=9999.0) is False


def test_panel_token_rejected_when_tampered_or_wrong_secret():
    token = mint_panel_token(SECRET, ttl_seconds=1800, now=1000.0)
    exp, _, sig = token.partition(".")
    assert verify_panel_token(SECRET, f"{exp}.{sig[:-1]}0", now=1000.0) is False  # bad sig
    assert verify_panel_token(SECRET, f"9999999999.{sig}", now=1000.0) is False   # bumped exp
    assert verify_panel_token("other-secret", token, now=1000.0) is False
    assert verify_panel_token(SECRET, "garbage", now=1000.0) is False
    assert verify_panel_token("", token, now=1000.0) is False


def test_timestamp_fresh_ms_window():
    now = 1_000_000.0
    assert timestamp_fresh_ms(str(int(now * 1000)), 300, now=now) is True
    assert timestamp_fresh_ms(str(int((now - 299) * 1000)), 300, now=now) is True
    assert timestamp_fresh_ms(str(int((now + 299) * 1000)), 300, now=now) is True
    assert timestamp_fresh_ms(str(int((now - 301) * 1000)), 300, now=now) is False
    assert timestamp_fresh_ms("", 300, now=now) is False
    assert timestamp_fresh_ms("not-a-number", 300, now=now) is False


def test_chatwoot_signature_scheme():
    # sha256=HMAC_SHA256(secret, "{timestamp}.{raw_body}")
    secret, ts, body = "cw-secret", "1700000000", b'{"event":"contact_created"}'
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    good = f"sha256={mac}"
    assert verify_chatwoot_signature(secret, ts, body, good) is True
    assert verify_chatwoot_signature(secret, ts, body, mac) is False        # missing prefix
    assert verify_chatwoot_signature(secret, ts, body, "sha256=00") is False
    assert verify_chatwoot_signature("", ts, body, good) is False


def test_twenty_signature_scheme():
    # bare hex HMAC_SHA256(secret, "{timestamp}:{raw_body}")
    secret, ts, body = "tw-secret", "1700000000000", b'{"eventName":"person.updated"}'
    good = hmac.new(secret.encode(), f"{ts}:".encode() + body, hashlib.sha256).hexdigest()
    assert verify_twenty_signature(secret, ts, body, good) is True
    assert verify_twenty_signature(secret, ts, body, f"sha256={good}") is False  # no prefix expected
    assert verify_twenty_signature(secret, "1700000000001", body, good) is False  # ts is signed
    assert verify_twenty_signature(secret, ts, body, "") is False
