# Shared, process-wide client instances. Constructed in the app lifespan
# (app/main.py) and used by routers/services. Kept here to avoid import cycles.
from __future__ import annotations

from app.clients.chatwoot_client import ChatwootClient
from app.clients.twenty_client import TwentyClient

twenty: TwentyClient | None = None
chatwoot: ChatwootClient | None = None


def require_twenty() -> TwentyClient:
    if twenty is None:
        raise RuntimeError("Twenty client not initialized")
    return twenty


def require_chatwoot() -> ChatwootClient:
    if chatwoot is None:
        raise RuntimeError("Chatwoot client not initialized")
    return chatwoot
