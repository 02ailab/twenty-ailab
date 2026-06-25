#!/usr/bin/env bash
# Deploy / update Twenty CRM on the Saldo k3s VPS (crm.saldo.chat).
# Run on the VPS from /root/twenty after WinSCP upload (see deploy/README.md).
# Idempotent: safe to re-run for upgrades (helm upgrade --install).
#
# Requires the DB app password (kept out of git) in the environment:
#   DB_APP_PASSWORD=<clean alphanumeric, no special chars> bash deploy/deploy-twenty.sh
#
# NOTE: on a FRESH install the chart's DB-bootstrap init is broken — after the
# first run you must create the database/role manually (deploy/README.md Step 4b).
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"

RELEASE="${RELEASE:-twenty}"
NAMESPACE="${NAMESPACE:-twenty}"
CHART_PATH="${CHART_PATH:-./helm/twenty}"
VALUES_FILE="${VALUES_FILE:-./deploy/values-twenty-saldo.yaml}"

echo "==> Preflight"
command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
command -v helm >/dev/null || { echo "helm not found" >&2; exit 1; }
[ -d "${CHART_PATH}" ] || { echo "Chart not found at ${CHART_PATH}" >&2; exit 1; }
[ -f "${VALUES_FILE}" ] || { echo "Values not found at ${VALUES_FILE}" >&2; exit 1; }
[ -n "${DB_APP_PASSWORD:-}" ] || { echo "DB_APP_PASSWORD env var is required (DB app password)" >&2; exit 1; }

kubectl get nodes >/dev/null
kubectl get clusterissuer letsencrypt-prod >/dev/null \
  || { echo "ClusterIssuer letsencrypt-prod missing — cert-manager not ready" >&2; exit 1; }

echo "==> Chatwoot health (must stay up; this deploy must not affect it)"
if ! curl -sI https://chat.saldo.chat | grep -qi "HTTP/"; then
  echo "WARNING: chat.saldo.chat did not respond — investigate before continuing." >&2
fi

echo "==> Deploying release '${RELEASE}' into namespace '${NAMESPACE}'"
# No --wait: a dropped SSH session must not kill the release mid-apply.
# db image tag + redis persistence live in the values file; only the secret
# app password is passed here.
helm upgrade --install "${RELEASE}" "${CHART_PATH}" \
  -n "${NAMESPACE}" --create-namespace \
  -f "${VALUES_FILE}" \
  --set db.internal.appPassword="${DB_APP_PASSWORD}"

echo "==> Status"
kubectl -n "${NAMESPACE}" get pods,svc,ingress,certificate,pvc

echo "==> Availability check"
curl -sI https://crm.saldo.chat | grep -i "HTTP/" || true

echo "==> Done. If certificate twenty-tls is not READY yet, give cert-manager 1-2 min and re-check:"
echo "    kubectl -n ${NAMESPACE} get certificate twenty-tls"
