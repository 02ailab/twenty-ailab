# Unit tests for the panel rate-limit client-key derivation (P2-7 / PLAT-6 step 4).
# Pure-function tests: no network, no FastAPI app — a tiny request stub is enough.
from __future__ import annotations

from app.routers.panel import _client_key


class _Client:
    def __init__(self, host: str) -> None:
        self.host = host


class _Req:
    # Minimal stand-in for starlette Request: headers.get + .client.host are all _client_key uses.
    def __init__(self, xff: str | None = None, peer: str | None = "10.42.0.1") -> None:
        self.headers = {"x-forwarded-for": xff} if xff is not None else {}
        self.client = _Client(peer) if peer is not None else None


def test_single_real_ip_when_traefik_overwrites():
    # External traffic, Traefik rewrote XFF to the real client -> that's the key.
    assert _client_key(_Req("203.0.113.7")) == "203.0.113.7"


def test_spoofed_left_entry_is_ignored():
    # Attacker prepends a forged IP; Traefik appended the real one on the right.
    assert _client_key(_Req("1.2.3.4, 203.0.113.7")) == "203.0.113.7"


def test_rotating_spoof_cannot_change_key():
    # Two requests with different forged leftmost entries must map to the SAME bucket,
    # so the per-IP enumeration brake holds.
    a = _client_key(_Req("9.9.9.9, 203.0.113.7"))
    b = _client_key(_Req("8.8.8.8, 203.0.113.7"))
    assert a == b == "203.0.113.7"


def test_trusted_proxy_hops_skipped_from_right():
    # Real client, then trusted pod-network hops appended -> skip the 10.42.* hops.
    assert _client_key(_Req("203.0.113.7, 10.42.0.5, 10.42.0.1")) == "203.0.113.7"


def test_falls_back_to_peer_without_xff():
    assert _client_key(_Req(None, peer="198.51.100.2")) == "198.51.100.2"


def test_all_trusted_falls_back_to_peer():
    # Only pod-network hops present (internal caller) -> fall through to the peer host.
    assert _client_key(_Req("10.42.0.9", peer="10.42.0.9")) == "10.42.0.9"


def test_unknown_when_no_xff_no_peer():
    assert _client_key(_Req(None, peer=None)) == "unknown"
