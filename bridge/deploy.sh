#!/usr/bin/env bash
# Operator entry point: run on the VPS from the bridge repo root after WinSCP upload.
#   cd /root/twenty-bridge && bash deploy.sh            # full build + deploy
#   bash deploy.sh --secret-only                         # only refresh the Secret
set -euo pipefail

export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
NAMESPACE="${NAMESPACE:-twenty-bridge}"

# Normalize CRLF from Windows/WinSCP on scripts and .env.
sed -i 's/\r$//' scripts/*.sh deploy.sh .env 2>/dev/null || true

echo "==> Preflight"
command -v kubectl >/dev/null || { echo "kubectl not found" >&2; exit 1; }
[ -f .env ] || { echo ".env missing — copy .env.example to .env and fill secrets" >&2; exit 1; }
# Don't break the live apps: confirm Chatwoot + Twenty are up first.
curl -sI https://chat.saldo.chat | grep -qi "HTTP/" || echo "WARN: chat.saldo.chat not responding"
curl -sI https://crm.saldo.chat  | grep -qi "HTTP/" || echo "WARN: crm.saldo.chat not responding"

if [ "${1:-}" = "--secret-only" ]; then
  bash scripts/k8s_create_secret.sh
  kubectl -n "${NAMESPACE}" rollout restart deployment/twenty-bridge-api || true
  exit 0
fi

bash scripts/deploy_local_k3s.sh

echo "==> Pods"
kubectl -n "${NAMESPACE}" get pods
echo "==> Smoke"
bash scripts/smoke_port_forward.sh
echo "==> Done. Internal webhook URL for Chatwoot/Twenty:"
echo "    http://twenty-bridge-api.${NAMESPACE}.svc.cluster.local:8000/webhooks/chatwoot"
