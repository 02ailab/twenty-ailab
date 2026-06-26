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

    # --- Twenty ---
    twenty_base_url: str = "http://twenty-server.twenty.svc.cluster.local:3000"
    twenty_api_key: str  # secret — Bearer token from Twenty Settings -> APIs
    # Optional: direction B (Twenty -> Chatwoot) not built yet, may be empty.
    twenty_webhook_secret: str = ""  # secret — verifies inbound Twenty webhooks
    # Public Twenty URL, used to build "open in CRM" links shown in the panel.
    twenty_public_url: str = "https://crm.saldo.chat"

    # --- Panel (public) ---
    # Public base URL of this service, used in the Dashboard App iframe URL.
    bridge_public_url: str = "https://bridge.saldo.chat"
    # Shared secret embedded in the Dashboard App URL to gate the public panel.
    # Required — the panel is the only public surface; empty would disable its gate.
    panel_shared_secret: str  # secret

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
