#!/usr/bin/env bash
# Positive-chain probe wrapper (work order twenty-ailab P2-8) — called by the
# orchestrator from orchestration-check.sh --positive.
#
# Streams orch_positive_b.py INTO the running twenty-bridge-api pod and runs it there,
# where the app deps (httpx, asyncpg) and all secrets (env) already live. The HMAC secret
# is used to sign in-pod and NEVER leaves the pod; only a single JSON result line comes
# back on stdout. Exit code = probe result (0 ok, non-zero failure). The probe creates a
# synthetic test-orch-* contact/Person and cleans it (and the bridge mapping) up itself.
#
# Usage (on the VPS):
#   bash /root/twenty-bridge/scripts/orch-positive-b.sh
set -euo pipefail

NS="${BRIDGE_NS:-twenty-bridge}"
DEPLOY="${BRIDGE_DEPLOY:-deploy/twenty-bridge-api}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROBE="$SCRIPT_DIR/orch_positive_b.py"

[ -f "$PROBE" ] || { echo "probe not found: $PROBE" >&2; exit 2; }

# stdin = the probe source; stdout = its JSON result line; stderr = progress.
kubectl -n "$NS" exec -i "$DEPLOY" -- python - < "$PROBE"
