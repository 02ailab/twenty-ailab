#!/usr/bin/env bash
# Create/update the twenty-bridge-secrets Secret from .env (secret keys only).
# Run on the VPS from the bridge repo root. Never commits secrets.
set -euo pipefail

NAMESPACE="${NAMESPACE:-twenty-bridge}"
SECRET_NAME="${SECRET_NAME:-twenty-bridge-secrets}"
ENV_FILE="${ENV_FILE:-.env}"

[ -f "${ENV_FILE}" ] || { echo "Missing ${ENV_FILE}" >&2; exit 1; }

# Parse KEY=VALUE pairs as DATA — never source the dotenv (sourcing would execute
# it as shell and mangle/execute values containing $, backticks, spaces, etc.).
read_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n1)" || true
  line="${line#*=}"
  printf '%s' "${line%$'\r'}"
}

# Always required.
required=(POSTGRES_PASSWORD PANEL_SHARED_SECRET)
# Optional now (e.g. TWENTY_API_KEY is blocked on Twenty admin creation).
optional=(CHATWOOT_API_TOKEN CHATWOOT_WEBHOOK_SECRET TWENTY_API_KEY TWENTY_WEBHOOK_SECRET)

args=()
for key in "${required[@]}"; do
  val="$(read_env "${key}")"
  case "${val}" in
    ""|replace-with-*|replace_me_*)
      echo "Required secret ${key} is empty/placeholder in ${ENV_FILE}" >&2; exit 1;;
  esac
  args+=(--from-literal="${key}=${val}")
done
for key in "${optional[@]}"; do
  val="$(read_env "${key}")"
  case "${val}" in
    ""|replace-with-*) echo "note: ${key} not set yet (ok for now)";;
    *) args+=(--from-literal="${key}=${val}");;
  esac
done

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "${NAMESPACE}" create secret generic "${SECRET_NAME}" \
  "${args[@]}" --dry-run=client -o yaml | kubectl apply -f -
echo "Secret ${SECRET_NAME} applied in ${NAMESPACE}."
