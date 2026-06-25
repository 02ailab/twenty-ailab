# Deploy Twenty CRM to the Saldo k3s VPS (`crm.saldo.chat`)

Operator runbook for tracker card **#227 — "Развернуть TwentyCRM в Kubernetes для
домена crm.saldo.chat"**. Same VPS and tooling as Chatwoot (k3s + Traefik +
cert-manager + Helm). Twenty is deployed from the **official in-repo Helm chart**
`packages/twenty-docker/helm/twenty` with the values file in this directory.

Workflow matches the rest of the platform: **WinSCP upload → PuTTY → helm**.
Nothing here contains secrets; the chart generates the DB password and app token
in-cluster.

## Subtask map (#227)

| # | Subtask | Covered by |
|---|---------|-----------|
| 1 | DNS-запись `crm.saldo.chat` | **Manual** — A record at Namecheap (Step 1) |
| 2 | Ingress-контроллер + cert-manager | Already live — verify in Step 2 |
| 3 | PostgreSQL в namespace `twenty` | Chart internal `db` (Step 4 `helm install`) |
| 4 | Redis в namespace `twenty` | Chart internal `redisInternal` (Step 4) |
| 5 | Приложение TwentyCRM | Chart `server` + `worker` (Step 4) |
| 6 | TLS-сертификат + доступность | cert-manager `twenty-tls` + checks (Step 5) |
| 7 | Зафиксировать итоговую конфигурацию | Step 7 + `SERVER_ARCHITECTURE_twenty_section.md` |

---

## Step 0 — What to upload via WinSCP

You do **not** need the whole monorepo on the VPS. Upload only:

```text
packages/twenty-docker/helm/twenty/   ->  /root/twenty/helm/twenty/
deploy/values-twenty-saldo.yaml       ->  /root/twenty/values-twenty-saldo.yaml
deploy/deploy-twenty.sh               ->  /root/twenty/deploy-twenty.sh   (optional helper)
```

Use a WinSCP text mode that converts to Unix LF, or fix line endings after upload:

```bash
sed -i 's/\r$//' /root/twenty/deploy-twenty.sh
```

All commands below assume:

```bash
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
cd /root/twenty
```

## Step 1 — DNS (subtask 1)

At Namecheap, add an **A record** for the `crm` host pointing at the VPS
(same IP as `chat.saldo.chat`):

```text
Type: A   Host: crm   Value: 159.195.80.68   TTL: Automatic
```

Wait for it to resolve **before** deploying — cert-manager's HTTP-01 challenge
needs the host publicly resolvable to issue TLS:

```bash
dig @1.1.1.1 +short crm.saldo.chat      # expect 159.195.80.68
```

## Step 2 — Preflight: ingress + cert-manager + Chatwoot health (subtask 2)

```bash
kubectl get nodes
kubectl -n kube-system get pods | grep -i traefik          # ingress controller present
kubectl get clusterissuer letsencrypt-prod                 # cert-manager issuer READY=True
# Do not proceed if Chatwoot is unhealthy — this deploy must not touch it:
helm status chatwoot -n chatwoot
curl -sI https://chat.saldo.chat | grep -i "HTTP/"
```

## Step 3 — DB app password (pin it)

The chart auto-generates the app/access token (Secret `tokens`) — leave that alone.
But **pin the PostgreSQL app password** so it stays identical across every
`helm upgrade` and matches the role we create in Step 4b. Generate a clean
**alphanumeric** value once (special chars break the `postgres://` URL):

```bash
openssl rand -hex 16     # e.g. 7b05011dcad570054816cd4c6aa9a036 — save this
```

Pass it as `--set db.internal.appPassword=<value>` on EVERY helm command for this
release. (Do not commit it.)

## Step 4 — Deploy PostgreSQL + Redis + Twenty (subtasks 3, 4, 5)

Install. Note the extra `--set` flags — they work around chart defaults that
otherwise break this deploy (see "Known issues" below). Run WITHOUT `--wait` so a
dropped SSH session can't kill the release mid-apply:

```bash
APP_PW=<the value from Step 3>

helm upgrade --install twenty ./helm/twenty \
  -n twenty --create-namespace \
  -f values-twenty-saldo.yaml \
  --set db.internal.image.tag=v0.43.5 \
  --set redisInternal.persistence.enabled=false \
  --set db.internal.appPassword="$APP_PW"
```

Watch it come up:

```bash
kubectl -n twenty get pods -w
```

`twenty-db` and `twenty-redis` reach `Running`. **`twenty-server` will
CrashLoopBackOff** at this point — that is expected, because the chart's
DB-bootstrap init container is broken and never creates the database/role.
Fix it in Step 4b.

## Step 4b — Create the database + role manually (REQUIRED — chart init is broken)

The chart's `ensure-database-exists` init container uses psql variable
interpolation (`:'db'`, `:'app_user'`) that fails ("syntax error at or near :"),
so the `twenty` database and `twenty_app_user` role are never created. Postgres
then reports "password authentication failed for user twenty_app_user" (it hides
the fact that the role doesn't exist). Create them by hand:

```bash
PW=$(kubectl -n twenty get secret twenty-db-url -o jsonpath='{.data.appPassword}' | base64 -d)

kubectl -n twenty exec deploy/twenty-db -- bash -c "PGPASSWORD=postgres psql -U postgres -h 127.0.0.1 -d postgres -v ON_ERROR_STOP=1 <<SQL
CREATE USER twenty_app_user WITH PASSWORD '$PW';
CREATE DATABASE twenty OWNER twenty_app_user;
SQL"

kubectl -n twenty exec deploy/twenty-db -- bash -c "PGPASSWORD=postgres psql -U postgres -h 127.0.0.1 -d twenty -v ON_ERROR_STOP=1 <<SQL
CREATE SCHEMA IF NOT EXISTS core AUTHORIZATION twenty_app_user;
GRANT ALL PRIVILEGES ON DATABASE twenty TO twenty_app_user;
GRANT ALL ON SCHEMA public TO twenty_app_user;
GRANT ALL ON SCHEMA core TO twenty_app_user;
SQL"

kubectl -n twenty rollout restart deploy/twenty-server deploy/twenty-worker
```

Now `twenty-server` runs migrations and reaches `Running`
(`[NestApplication] Nest application successfully started`). The role/database
live on the DB PVC and survive restarts and `helm upgrade`; only a full namespace
teardown loses them (then re-run this step).

## Step 5 — TLS + availability (subtask 6)

```bash
kubectl -n twenty get ingress,svc,pods
kubectl -n twenty get certificate          # twenty-tls -> READY=True (may take 1-2 min)
kubectl -n twenty describe certificate twenty-tls | tail -20   # if not ready, inspect events

curl -sI https://crm.saldo.chat | grep -i "HTTP/"   # expect 200 (or app redirect)
```

If the certificate is stuck, the usual cause is DNS not yet resolving (Step 1) or
a port-80 HTTP-01 challenge issue — check:

```bash
kubectl -n twenty get order,challenge
```

## Step 6 — First admin + hardening

1. Open `https://crm.saldo.chat` and sign up — the **first** account becomes the
   workspace admin.
2. Signup hardening: Twenty has **no global "disable signup" env var** (unlike
   Chatwoot's `ENABLE_ACCOUNT_SIGNUP`). Access for new users is controlled
   per-workspace (invitations / invite-link settings in **Settings → Members**).
   After creating the admin, review those settings and disable the public invite
   link if it is on. (Track exact lockdown during bring-up.)

## Step 7 — Record the final config (subtask 7)

After go-live, fold the prepared section
[`SERVER_ARCHITECTURE_twenty_section.md`](./SERVER_ARCHITECTURE_twenty_section.md)
into the canonical `general_docs/SERVER_ARCHITECTURE.md`, filling in the real
runtime facts:

```bash
kubectl -n twenty get pods,svc,ingress,pvc,certificate
helm list -n twenty           # release name + revision
```

## Update an already-deployed Twenty

```bash
cd /root/twenty
# edit values-twenty-saldo.yaml as needed, then:
helm upgrade --install twenty ./helm/twenty -n twenty -f values-twenty-saldo.yaml --wait --timeout 20m
```

To bump the Twenty version, change `image.tag` in the values file and upgrade.

## Rollback / removal

```bash
helm history twenty -n twenty
helm rollback twenty <REVISION> -n twenty

# Full removal (CAUTION: deletes data PVCs with the namespace):
helm uninstall twenty -n twenty
kubectl delete namespace twenty
```

## Isolation guarantee

This deploy creates only the `twenty` namespace and one Ingress for
`crm.saldo.chat`. It must not modify the `chatwoot` namespace, its ingress, or
its certificate. Re-check after deploy:

```bash
curl -sI https://chat.saldo.chat | grep -i "HTTP/"
kubectl get pods -n chatwoot
```
