# DRAFT — Twenty CRM section for `general_docs/SERVER_ARCHITECTURE.md`

Fold this into the canonical server map after Twenty is live, filling the
`<TBD>` runtime facts from the cluster. Suggested placement: a new `§8B Twenty
CRM Deployment` alongside the Chatwoot (§8) and llm-core (§9) sections.

---

## 8B. Twenty CRM Deployment

> **Status:** LIVE since 2026-06-25 (Helm release `twenty`, revision 2). Public via
> Traefik at `https://crm.saldo.chat` (HTTP/2 200, TLS `twenty-tls` READY). Deployed
> from the official in-repo Helm chart (`packages/twenty-docker/helm/twenty`,
> appVersion `v1.14.0`, image `twentycrm/twenty`). Vanilla Twenty — no custom fork
> layer yet. Purpose of this instance: CRM for the Saldo platform; future bridge
> service will sync it with Chatwoot.

### 8B.1. Status

| Item | Value |
|------|-------|
| Namespace | `twenty` |
| Public URL | `https://crm.saldo.chat` |
| Exposure | public via Traefik (like Chatwoot) |
| Image | `twentycrm/twenty:v1.14.0` (official, pulled from Docker Hub) |
| Deploy method | Helm release `twenty` (chart `packages/twenty-docker/helm/twenty`) |
| Values file | `twenty-ailab/deploy/values-twenty-saldo.yaml` |
| Database | chart-internal PostgreSQL (`twentycrm/twenty-postgres-spilo`, pgvector) |
| Queue/cache | chart-internal Redis (`redis/redis-stack-server`) |
| TLS | cert-manager `twenty-tls` via ClusterIssuer `letsencrypt-prod` (READY) |
| Helm revision | 2 (rev 1 = ingress-off bring-up, rev 2 = ingress enabled) |

### 8B.2. Kubernetes Resources

| Kind | Name | Purpose |
|------|------|---------|
| Namespace | `twenty` | Isolated namespace for Twenty |
| Deployment | `twenty-server` | Twenty API/app (port 3000); runs DB migrations + cron on start |
| Deployment | `twenty-worker` | Background worker (`yarn worker:prod`) |
| Deployment | `twenty-db` | Internal PostgreSQL (pgvector spilo) |
| Deployment | `twenty-redis` | Internal Redis |
| Service | `twenty-server` | ClusterIP `:3000` |
| Service | `twenty-db` / `twenty-redis` | Internal PG / Redis services |
| Ingress | `twenty` | host `crm.saldo.chat` -> `twenty-server:3000` |
| Secret | `tokens` | chart-generated app/access token (= `APP_SECRET`) |
| Secret | `twenty-db-url` | chart-generated PG app password + connection URL |
| Secret | `twenty-tls` | cert-manager TLS keypair |
| PVC | `twenty-server`, `twenty-db`, `twenty-redis`, `twenty-docker-data` | local-path storage |

### 8B.3. Key Config

```text
SERVER_URL = https://crm.saldo.chat   # set explicitly (avoids the chart's ":443" suffix)
STORAGE_TYPE = local                  # local-path PVC; S3 is the future move
PG_DATABASE_URL / REDIS_URL           # auto-wired by the chart to internal services
APP_SECRET                            # from chart-generated `tokens` Secret
```

### 8B.4. Deploy / Update

```bash
# on VPS, in /root/twenty (chart + values uploaded via WinSCP)
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
helm upgrade --install twenty ./helm/twenty -n twenty --create-namespace \
  -f values-twenty-saldo.yaml --wait --timeout 20m
```

Full runbook: `twenty-ailab/deploy/README.md`.

### 8B.5. Isolation From Chatwoot

Creates only namespace `twenty` and the `crm.saldo.chat` Ingress. Does not touch
the `chatwoot` namespace, ingress, or certificate. Post-deploy regression check:
`curl -I https://chat.saldo.chat` + `kubectl get pods -n chatwoot`.

### 8B.6. Deploy gotchas (community chart is rough — all worked around)

1. Chart default DB image tag `3.3-p2` does not exist on Docker Hub →
   ImagePullBackOff. Pinned `db.internal.image.tag=v0.43.5`.
2. Chart Redis is a Deployment with a RWO PVC → redeploys deadlock (old pod holds
   the volume). Disabled Redis persistence (`redisInternal.persistence.enabled=false`).
3. **Chart `ensure-database-exists` init container is broken** — its psql variable
   interpolation (`:'db'`, `:'app_user'`) fails, so the `twenty` DB and
   `twenty_app_user` role are never created (Postgres masks this as "password
   authentication failed"). Workaround: create DB + role manually as superuser
   `postgres/postgres`, then `rollout restart` server/worker. Full procedure in
   `twenty-ailab/deploy/README.md` Step 4b. Lives on the DB PVC; survives restarts
   and `helm upgrade`; lost only on namespace teardown.
4. Pin `db.internal.appPassword` (clean alphanumeric, no special chars) and pass it
   on every `helm upgrade` so the secret stays stable and matches the manual role.

### 8B.7. Follow-ups

- No global signup-disable env in Twenty; new-user access is per-workspace
  (Settings → Members). Lock down the public invite link after creating admin.
- PostgreSQL backup for `twenty` not yet automated (same gap as Chatwoot §12.1).
  Add a pg-backup CronJob like §9A.4 / §9B.4.
- Fix the chart init (so a clean redeploy auto-creates the DB/role) — or pre-seed.
- Bridge service (Twenty ↔ Chatwoot sync + Dashboard App panel) is a later phase.
