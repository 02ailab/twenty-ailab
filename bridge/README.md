# twenty-bridge — Chatwoot ↔ Twenty integration service

Standalone service that bridges the live **Chatwoot** (`chat.saldo.chat`) and
**Twenty CRM** (`crm.saldo.chat`) on the Saldo k3s cluster. It does **not** modify
either fork — it talks to both over the network.

## Scope (iteration 1)

- **Sync Chatwoot → Twenty**: on Chatwoot `contact_created` / `contact_updated` /
  `conversation_created`, upsert a **Person** (+ linked **Company**) in Twenty.
  Store the Twenty id back on the Chatwoot contact
  (`additional_attributes.external.twenty_id`) and in the bridge's own DB.
- **Panel**: a Chatwoot **Dashboard App** iframe that shows the contact's Twenty
  card inside the conversation view (served by this service, which holds the
  Twenty API key server-side).

Out of scope for now (later): Twenty → Chatwoot write-back (bidirectional),
conversation → Note sync.

## Why all traffic is mostly internal

Chatwoot, Twenty and this bridge all run in the **same k3s cluster**, so:

- Chatwoot → bridge webhooks use cluster DNS (private, no public ingress).
- bridge → Twenty (`http://twenty-server.twenty.svc.cluster.local:3000`) is private.
- bridge → Chatwoot REST (`http://chatwoot.chatwoot.svc.cluster.local`) is private.
- **Only the panel** needs public HTTPS (it loads in the agent's browser) →
  one Traefik ingress + cert-manager TLS, e.g. `bridge.saldo.chat`.

## Architecture

```text
Chatwoot --webhook(internal)--> /webhooks/chatwoot --> upsert Person/Company --> Twenty
Twenty   --webhook(internal)--> /webhooks/twenty   --> (later) update Chatwoot
Agent browser --iframe(public)--> /panel?... --> bridge queries Twenty --> CRM card
mapping (chatwoot_contact_id <-> twenty_person_id) stored in the bridge's own Postgres
```

## Stack

FastAPI + httpx + own PostgreSQL. Deployed to k3s in its own namespace
(`twenty-bridge`) following `general_docs/SALDO_K3S_DEPLOYMENT_GUIDE.md`
(WinSCP → docker build on VPS → `k3s ctr images import` → `kubectl apply`).

## Status

**Iteration 1 — LIVE since 2026-06-26** (namespace `twenty-bridge`, panel at
`bridge.saldo.chat`). Chatwoot→Twenty contact+company sync and the Dashboard App
panel work end-to-end in production. Includes code-review hardening: panel auth
(secret validated, no leak), strict webhook HMAC, defensive parsing, required
config, and container `securityContext`. Direction B (Twenty→Chatwoot) is stubbed
in `/webhooks/twenty` for a later iteration.

Live deployment map: `general_docs/SERVER_ARCHITECTURE.md` §8C.

## Prerequisites (all satisfied — live)

| Item | Where from | Status |
|------|-----------|--------|
| Twenty API key | Twenty → Settings → APIs | ✅ configured |
| Chatwoot API access token | Chatwoot → Profile / Agent Bot token | ✅ configured |
| Chatwoot `account_id` | Chatwoot URL / API | ✅ `1` |
| Public host for panel | DNS A-record `bridge.saldo.chat` → VPS IP | ✅ live (TLS `bridge-tls` Ready) |

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
      webhooks.py      # /webhooks/chatwoot /webhooks/twenty (strict HMAC)
      panel.py         # /panel iframe + proxy (secret-gated, no fallback)
    services/
      sync.py          # Chatwoot->Twenty upsert logic
  deploy/k8s/twenty-bridge.yaml   # namespace, own Postgres, Deployment, Service, Ingress(/panel)
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
   `CHATWOOT_ACCOUNT_ID`; `TWENTY_API_KEY` once the Twenty admin exists).
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

