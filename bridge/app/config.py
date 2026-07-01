# Runtime configuration for twenty-bridge. Secrets come from env (k8s Secret),
# non-secret config from env/ConfigMap. Never hardcode tokens here.
from __future__ import annotations

from functools import lru_cache
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Chatwoot ---
    # Internal cluster DNS by default; works because the bridge runs in-cluster.
    chatwoot_base_url: str = "http://chatwoot.chatwoot.svc.cluster.local:3000"
    # Required (no default) — fail fast at startup rather than booting misconfigured.
    chatwoot_account_id: int
    chatwoot_api_token: str  # secret — agent/admin access token
    # HMAC secret Chatwoot signs outbound webhooks with (per-webhook secret).
    chatwoot_webhook_secret: str  # secret
    # Public Chatwoot URL, used to build the "open chat" link shown on the Twenty
    # Person card (must be the public host, not the internal cluster DNS above).
    chatwoot_public_url: str = "https://chat.saldo.chat"

    # --- Twenty ---
    twenty_base_url: str = "http://twenty-server.twenty.svc.cluster.local:3000"
    twenty_api_key: str  # secret — Bearer token from Twenty Settings -> APIs
    # Optional: direction B (Twenty -> Chatwoot) not built yet, may be empty.
    twenty_webhook_secret: str = ""  # secret — verifies inbound Twenty webhooks
    # Replay/freshness window (seconds) for the public Twenty webhook. 0 = disabled
    # (default — enable only after confirming Twenty's timestamp semantics and node
    # clock skew, else valid webhooks could be rejected). E.g. 300 = ±5 min.
    twenty_webhook_max_age_seconds: int = 0
    # Public Twenty URL, used to build "open in CRM" links shown in the panel.
    twenty_public_url: str = "https://crm.saldo.chat"
    # API name of the Person "Links" field that holds the Chatwoot conversation
    # link. The operator creates this field once in Twenty (Settings -> Data model
    # -> Person, type "Links"); the bridge only populates it, best-effort.
    twenty_chatwoot_field: str = "chatwoot"

    # --- saldoClientId (cross-service client number; Twenty = authority) ---
    # Per-client number AAAA (4 digits, from 2000) assigned ONCE when the bridge first
    # creates a Person. Feature flag OFF until the operator creates the NUMBER field in
    # Twenty (Settings -> Data model -> Person) and flips this on — with it off the bridge
    # never reads/writes the field, so shipping the code before the field exists is safe.
    saldo_client_id_enabled: bool = False
    # API name of the Person NUMBER field holding the saldoClientId.
    twenty_saldo_client_id_field: str = "saldoClientId"

    # --- CRM-FULL (admin-only Twenty gets the full client card) ---
    # Twenty is admin-only, so anonymization there is counterproductive: enrich the Person
    # with the real Telegram identity. OFF until the operator creates the two TEXT fields.
    crm_full_enabled: bool = False
    # API names of the Person TEXT fields for the Telegram identity.
    twenty_telegram_username_field: str = "telegramUsername"
    twenty_telegram_id_field: str = "telegramId"

    # --- Notes (Chatwoot conversation -> Twenty Note) ---
    # On a Chatwoot conversation resolve, write the transcript to a Twenty Note on
    # the contact's Person (one note per conversation, body refreshed each resolve).
    note_sync_enabled: bool = True
    # Cap transcript length (keeps the most recent N messages) to bound note size.
    note_max_messages: int = 100

    # --- Panel (public) ---
    # Public base URL of this service, used in the Dashboard App iframe URL.
    bridge_public_url: str = "https://bridge.saldo.chat"
    # Shared secret embedded in the Dashboard App URL to gate the public panel.
    # Required — the panel is the only public surface; empty would disable its gate.
    panel_shared_secret: str  # secret
    # The /panel page mints a short-lived token (signed with panel_shared_secret)
    # so the durable secret leaves the repeatedly-called /panel/api URL. The agent
    # keeps the iframe open per conversation; 30 min covers that, after which the
    # page silently re-mints on a 401/403.
    panel_token_ttl_seconds: int = 1800
    # Per-IP cap on /panel/api/contact calls per minute (blunts id-enumeration).
    # 0 disables the limiter.
    panel_rate_limit_per_minute: int = 60

    # --- Database (own Postgres) ---
    postgres_host: str = "twenty-bridge-postgres"
    postgres_port: int = 5432
    postgres_user: str = "bridge"
    postgres_password: str  # secret
    postgres_db: str = "twenty_bridge"

    # --- Runtime ---
    log_level: str = "INFO"
    log_format: str = "json"
    http_timeout_seconds: float = 30.0

    @property
    def postgres_dsn(self) -> str:
        # URL-encode credentials so special chars (@ : / #) can't corrupt the DSN.
        user = quote(self.postgres_user, safe="")
        password = quote(self.postgres_password, safe="")
        return (
            f"postgresql://{user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
