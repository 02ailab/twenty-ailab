#!/usr/bin/env bash
# Port-forward the bridge and check health. Run from the bridge repo root on the VPS.
set -euo pipefail

NAMESPACE="${NAMESPACE:-twenty-bridge}"
LOCAL_PORT="${LOCAL_PORT:-18000}"

# Unique log file (avoids a fixed /tmp path that another user could pre-create/symlink).
PF_LOG="$(mktemp "${TMPDIR:-/tmp}/twenty-bridge-pf.XXXXXX.log")"
kubectl -n "${NAMESPACE}" port-forward svc/twenty-bridge-api "${LOCAL_PORT}:8000" >"${PF_LOG}" 2>&1 &
PF_PID=$!
trap 'kill ${PF_PID} 2>/dev/null || true; rm -f "${PF_LOG}"' EXIT
sleep 3

code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LOCAL_PORT}/healthz")"
echo "healthz -> ${code}"
[ "${code}" = "200" ] || { echo "health check failed" >&2; exit 1; }

# /readyz reflects DB readiness — enforce it (with a short retry for startup), so a
# broken rollout fails the smoke instead of silently passing.
ready=""
for attempt in 1 2 3 4 5; do
  ready="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${LOCAL_PORT}/readyz")"
  [ "${ready}" = "200" ] && break
  echo "readyz  -> ${ready} (attempt ${attempt}/5); retrying..."
  sleep 2
done
echo "readyz  -> ${ready}"
[ "${ready}" = "200" ] || { echo "readiness check failed" >&2; exit 1; }
