#!/usr/bin/env bash
# Build the twenty-bridge image on the VPS, import it into k3s, (re)create the
# Secret from .env, and apply the manifests. Run from the bridge repo root.
set -euo pipefail

IMAGE="${IMAGE:-twenty-bridge:local}"
TAR_PATH="${TAR_PATH:-/tmp/twenty-bridge-local.tar}"
NAMESPACE="${NAMESPACE:-twenty-bridge}"

if command -v docker >/dev/null 2>&1; then
  docker build -t "${IMAGE}" .
  docker save "${IMAGE}" -o "${TAR_PATH}"
elif command -v podman >/dev/null 2>&1; then
  podman build -t "${IMAGE}" .
  podman save "${IMAGE}" -o "${TAR_PATH}"
else
  echo "Neither docker nor podman is installed." >&2; exit 1
fi

sudo k3s ctr images import "${TAR_PATH}"

bash "$(dirname "$0")/k8s_create_secret.sh"
kubectl apply -f deploy/k8s/twenty-bridge.yaml
kubectl -n "${NAMESPACE}" rollout restart deployment/twenty-bridge-api
kubectl -n "${NAMESPACE}" rollout status deployment/twenty-bridge-api --timeout=180s
kubectl -n "${NAMESPACE}" get pods,svc,ingress
