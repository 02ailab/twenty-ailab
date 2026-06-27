# twenty-bridge — Chatwoot ↔ Twenty integration service

Standalone service that bridges the live **Chatwoot** (`chat.saldo.chat`) and
**Twenty CRM** (`crm.saldo.chat`) on the Saldo k3s cluster. It does **not** modify
either fork — it talks to both over the network.

## Scope

**Iteration 1 — Chatwoot → Twenty:**

- on Chatwoot `contact_created` / `contact_updated` / `conversation_created`, upsert
  a **Person** (+ linked **Company**) in Twenty. Store the Twenty id back on the
  Chatwoot contact (`additional_attributes.external.twenty_id`) and in the bridge DB.
- **Panel**: a Chatwoot **Dashboard App** iframe showing the contact's Twenty card.
  Admin-only — Chatwoot serves the Dashboard App to administrators only (server-side,
  fail-closed); the panel itself is secret-gated and never echoes the secret.

**Iteration 2 — Twenty → Chatwoot:**

- **Write-back**: on Twenty `person.updated`, push the basic identity (name / email /
  phone) back onto the **native** Chatwoot contact. Only basic identity — rich Twenty
  CRM data stays in the admin-only panel, so it never leaks to non-admin managers. The
  echo loop is broken by compare-before-write on both sides (skip if unchanged).
- **Chatwoot link on the Twenty card**: the bridge sets a Person "Links" field to the
  contact's Chatwoot conversation URL, so an admin can jump from the CRM card to the chat.

Out of scope (later): conversation → Note sync.

## Why all traffic is mostly internal

Chatwoot, Twenty and this bridge all run in the **same k3s cluster**, so:

- Chatwoot → bridge webhooks use cluster DNS (private, no public ingress).
- bridge → Twenty (`http://twenty-server.twenty.svc.cluster.local:3000`) is private.
- bridge → Chatwoot REST (`http://chatwoot.chatwoot.svc.cluster.local`) is private.
- **The panel** needs public HTTPS (it loads in the agent's browser) →
  one Traefik ingress + cert-manager TLS, e.g. `bridge.saldo.chat`.
- **`/webhooks/twenty` is also public** — Twenty's outbound SSRF guard blocks private
  ClusterIPs, so Twenty can only reach the bridge via the public host. The endpoint is
  HMAC-gated and fail-closed (no `TWENTY_WEBHOOK_SECRET` ⇒ rejects). `/webhooks/chatwoot`
  stays internal (Chatwoot reaches it via cluster DNS thanks to `SAFE_FETCH_ALLOW_PRIVATE_NETWORK`).

## Architecture

```text
Chatwoot --webhook(internal)--> /webhooks/chatwoot --> upsert Person/Company --> Twenty
                                                   \--> set Person "Links" -> Chatwoot chat URL
Twenty   --webhook(PUBLIC,HMAC)--> /webhooks/twenty --> write name/email/phone --> Chatwoot
Agent browser --iframe(public)--> /panel?... --> bridge queries Twenty --> CRM card
mapping (chatwoot_contact_id <-> twenty_person_id) stored in the bridge's own Postgres
anti-echo: compare-before-write on BOTH sides (skip if unchanged)
```

## Stack

FastAPI + httpx + own PostgreSQL. Deployed to k3s in its own namespace
(`twenty-bridge`) following `general_docs/SALDO_K3S_DEPLOYMENT_GUIDE.md`
(WinSCP → docker build on VPS → `k3s ctr images import` → `kubectl apply`).

## Status

**Iteration 1 — LIVE since 2026-06-26** (namespace `twenty-bridge`, panel at
`bridge.saldo.chat`). Chatwoot→Twenty contact+company sync and the Dashboard App
panel work end-to-end in production. Includes code-review hardening: panel auth
(secret validated, no leak; plus `no-referrer` so the panel URL/secret never leaks
to the Twenty origin), strict webhook HMAC, defensive parsing, required config, and
container `securityContext`.

**Iteration 2 — LIVE since 2026-06-27** — Twenty→Chatwoot write-back + Chatwoot
link on the Twenty card. Adds: `/webhooks/twenty` reverse sync (public, HMAC,
fail-closed), compare-before-write anti-echo on both directions, and a best-effort
Person "Links" field populated with the contact's latest Chatwoot conversation URL.

Live deployment map: `general_docs/SERVER_ARCHITECTURE.md` §8C.

## Prerequisites

| Item | Where from | Status |
|------|-----------|--------|
| Twenty API key | Twenty → Settings → APIs | ✅ configured (live) |
| Chatwoot API access token | Chatwoot → Profile / Agent Bot token | ✅ configured (live) |
| Chatwoot `account_id` | Chatwoot URL / API | ✅ `1` (live) |
| Public host for panel | DNS A-record `bridge.saldo.chat` → VPS IP | ✅ live (TLS `bridge-tls` Ready) |
| **Twenty webhook secret** (iter 2) | random string → `TWENTY_WEBHOOK_SECRET` + Twenty webhook config | ✅ set (live) |
| **Person "Links" field** (iter 2) | Twenty → Settings → Data model → Person → add field, type **Links**, API name `chatwoot` | ✅ created |
| **Twenty → bridge webhook** (iter 2) | Twenty → Settings → Developers → Webhooks → `person.updated` → public bridge URL | ✅ live (HMAC) |

## Layout

```text
bridge/
  app/
    main.py            # FastAPI app + lifespan + routers
    config.py          # settings (env)
    logging_setup.py   # structured JSON logging (LOGGING_INCIDENTINATOR §0.1)
    db.py              # Postgres pool + id-mapping tables
    clients/
      twenty_client.py   # Twenty REST client (verified composite-field shapes)
      chatwoot_client.py # Chatwoot REST client
    routers/
      health.py        # /healthz /readyz
      webhooks.py      # /webhooks/chatwoot (internal) + /webhooks/twenty (public, HMAC, fail-closed)
      panel.py         # /panel iframe + proxy (secret-gated, no fallback, no-referrer)
    services/
      sync.py          # Chatwoot->Twenty upsert + Twenty->Chatwoot write-back (compare-before-write)
  deploy/k8s/twenty-bridge.yaml   # namespace, own Postgres, Deployment, Service, Ingress(/panel + /webhooks/twenty)
  scripts/                        # k8s_create_secret.sh, deploy_local_k3s.sh, smoke_port_forward.sh
  deploy.sh                       # operator entry point (WinSCP -> bash deploy.sh)
  Dockerfile  pyproject.toml  .env.example
```

## Deploy (operator, WinSCP + PuTTY)

The bridge is custom code, so it is **built on the VPS** (unlike vanilla Twenty).
Per `general_docs/SALDO_K3S_DEPLOYMENT_GUIDE.md`:

1. DNS: add A-record `bridge.saldo.chat` → `159.195.80.68` (needed for panel TLS).
2. WinSCP-upload this `bridge/` folder to `/root/twenty-bridge/`.
3. On the VPS: `cd /root/twenty-bridge && cp .env.example .env && nano .env`
   (fill `POSTGRES_PASSWORD`, `PANEL_SHARED_SECRET`, `CHATWOOT_API_TOKEN`,
   `CHATWOOT_ACCOUNT_ID`, `TWENTY_API_KEY`; for iteration 2 also set
   `TWENTY_WEBHOOK_SECRET` — required, the public reverse webhook is fail-closed).
   `CHATWOOT_PUBLIC_URL` / `TWENTY_CHATWOOT_FIELD` default correctly for prod.
4. `bash deploy.sh` — builds the image, imports into k3s, creates the Secret,
   applies manifests, smokes `/healthz`. cert-manager issues `bridge-tls` once DNS
   resolves.

Secret-only refresh (e.g. after the Twenty API key arrives):
`bash deploy.sh --secret-only`.

## Configure Chatwoot (after deploy)

1. **Webhook** (Settings → Integrations → Webhooks, or API) →
   URL `http://twenty-bridge-api.twenty-bridge.svc.cluster.local:8000/webhooks/chatwoot`
   (cluster-internal), events: `contact_created`, `contact_updated`,
   `conversation_created`. Use the same secret as `CHATWOOT_WEBHOOK_SECRET`.
2. **Dashboard App** (Settings → Integrations → Dashboard Apps, or
   `POST /api/v1/accounts/:id/dashboard_apps`) →
   content `[{"type":"frame","url":"https://bridge.saldo.chat/panel?secret=<PANEL_SHARED_SECRET>"}]`.
   The panel then appears in the conversation view and shows the contact's Twenty card.
   `/panel` validates this secret (no fallback). If it is ever exposed, rotate
   `PANEL_SHARED_SECRET`, run a full `bash deploy.sh`, then update this URL via
   `PATCH /api/v1/accounts/:id/dashboard_apps/:id`.

## Backups

Per the platform standard `general_docs/BACKUP_STANDARD.md` (mandatory for all services).
twenty-bridge stores only a mapping DB and no uploaded files, so a **full** backup =
Postgres dump + the encrypted k8s Secret:

- Script: `bridge/deploy/backups/backup-twenty-bridge.sh` → copy to `/root/backups/bin/`
  on the VPS, cron via `/etc/cron.d/saldo-backups` (daily 03:00).
- Output: `/root/backups/twenty-bridge/{db,secrets}/` (DB `*.sql.gz`, Secret `*.yaml.age`),
  retention 7. Secrets are `age`-encrypted; the private key is kept **off** the server.
- Offsite: the platform-wide `offsite-sync.sh` ships the whole `/root/backups` tree weekly
  (stub until `OFFSITE_REMOTE` is set). See the standard for key setup and restore steps.

## Configure Twenty (after deploy — iteration 2)

1. **Person "Links" field** (Settings → Data model → Person → add field) → type
   **Links**, API name `chatwoot` (must match `TWENTY_CHATWOOT_FIELD`). The bridge
   populates it best-effort; if the field is missing it logs `twenty_link_field_unavailable`
   and the rest of the sync still succeeds.
2. **Webhook** (Settings → Developers → Webhooks) → operation `person.updated`
   (optionally `person.created`), target URL **public**
   `https://bridge.saldo.chat/webhooks/twenty` (NOT the cluster-internal address —
   Twenty's SSRF guard blocks private IPs), secret = `TWENTY_WEBHOOK_SECRET`.
3. **Hairpin check** — confirm Twenty pods can reach the public ingress from inside
   the cluster (single-node k3s usually allows this):
   `kubectl -n twenty exec deploy/twenty-server -- curl -sk -o /dev/null -w '%{http_code}\n' https://bridge.saldo.chat/panel`
   → expect `403` (reached the bridge, secret rejected) — anything other than a
   connection error means the hairpin works and the webhook will be delivered.

