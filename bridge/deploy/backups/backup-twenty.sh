#!/usr/bin/env bash
# Full backup of Twenty CRM (ns `twenty`) per general_docs/BACKUP_STANDARD.md.
# Twenty CRM is vanilla upstream (community helm chart) — this is an EXTERNAL host
# script that only READS its database/secrets; it never touches Twenty's code or chart.
#
# "Full" = Postgres globals (roles) + every application DB (pg_dump -Fc, restorable
# into a throwaway DB so restore-drill is non-destructive) + local attachments +
# age-encrypted Secrets. Mirrors the twenty-bridge exemplar (§7.1) with the Twenty
# specifics: twenty-db is a Deployment running Zalando spilo (not a StatefulSet), the
# superuser creds live in the `twenty-db-superuser` Secret, and uploads are on the
# `twenty-server` PVC (STORAGE_TYPE=local).
#
# Deploy: copy to /root/backups/bin/ on the VPS, cron via /etc/cron.d/saldo-backups-twenty.
# Requires: kubectl (host), age, an age public key at /root/backups/bin/age-recipient.txt.
#
# ⚠️ THREE live-only facts are overridable below and VERIFIED in preflight (fail-loud,
#    never a silent/empty backup). Confirm them on the first run:
#      1. Secret key names in `twenty-db-superuser`  (SUPERUSER_USER_KEY / SUPERUSER_PASS_KEY)
#      2. Attachments path inside the twenty-server pod (TWENTY_STORAGE_PATH)
#      3. (app DB names are DISCOVERED at runtime, so not guessed)
set -euo pipefail

SVC="twenty"; NS="${NS:-twenty}"
ROOT="/root/backups"; DEST="$ROOT/$SVC"; BIN="$ROOT/bin"
LOG="$BIN/backup.log"; RECIP="$BIN/age-recipient.txt"
RETENTION="${BACKUP_RETENTION:-7}"

# --- live objects (override via env if they ever differ from SERVER_ARCHITECTURE §8B) ---
DB_DEPLOY="${TWENTY_DB_DEPLOY:-deploy/twenty-db}"
SERVER_DEPLOY="${TWENTY_SERVER_DEPLOY:-deploy/twenty-server}"
SUPERUSER_SECRET="${TWENTY_SUPERUSER_SECRET:-twenty-db-superuser}"
SUPERUSER_USER_KEY="${SUPERUSER_USER_KEY:-username}"
SUPERUSER_PASS_KEY="${SUPERUSER_PASS_KEY:-password}"
TWENTY_STORAGE_PATH="${TWENTY_STORAGE_PATH:-/app/packages/twenty-server/.local-storage}"
# Secrets to capture (TLS skipped on purpose — cert-manager reissues it; same as chatwoot).
SECRET_NAMES="${TWENTY_SECRET_NAMES:-tokens twenty-db-url twenty-db-superuser}"

ts()  { date +%Y%m%d-%H%M%S; }
log() { echo "$(date -Iseconds) backup[$SVC] $*" | tee -a "$LOG"; }
# `|| true`: optional layers (files) may have an empty dir; prune must not trip set -e.
prune() { ls -1t "$1"/*"$2" 2>/dev/null | tail -n +$((RETENTION+1)) | xargs -r rm -f || true; }

mkdir -p "$DEST/db" "$DEST/files" "$DEST/secrets"
chmod 700 "$DEST" "$DEST/db" "$DEST/files" "$DEST/secrets"
STAMP="$(ts)"

# --- resolve superuser creds host-side (never echoed) ---
b64d() { kubectl -n "$NS" get secret "$SUPERUSER_SECRET" -o "jsonpath={.data.$1}" 2>/dev/null | base64 -d 2>/dev/null; }
PGUSER_VAL="$(b64d "$SUPERUSER_USER_KEY")"
PGPASS_VAL="$(b64d "$SUPERUSER_PASS_KEY")"
if [ -z "$PGUSER_VAL" ] || [ -z "$PGPASS_VAL" ]; then
  log "ERROR superuser creds not found in secret/$SUPERUSER_SECRET (keys '$SUPERUSER_USER_KEY'/'$SUPERUSER_PASS_KEY')."
  log "      inspect available keys: kubectl -n $NS get secret $SUPERUSER_SECRET -o 'jsonpath={.data}' ; then set SUPERUSER_USER_KEY/SUPERUSER_PASS_KEY"
  exit 1
fi

# Run a psql/pg_dump* inside the twenty-db pod with the superuser password supplied via
# env on the remote shell (kept out of this script's logs; visible only to root in that pod).
in_db() { kubectl -n "$NS" exec -i "$DB_DEPLOY" -- env PGPASSWORD="$PGPASS_VAL" "$@"; }

# --- globals (roles/tablespaces) — needed so a restore recreates the app role ---
GLOB="$DEST/db/$SVC-db-globals-$STAMP.sql.gz"
if in_db pg_dumpall -U "$PGUSER_VAL" --globals-only | gzip > "$GLOB"; then
  chmod 600 "$GLOB"; log "globals ok $GLOB ($(du -h "$GLOB" | cut -f1))"
else
  log "ERROR pg_dumpall --globals-only failed"; rm -f "$GLOB"; exit 1
fi

# --- per application DB (discovered, not guessed; custom format = drill-restorable) ---
DBS="$(in_db psql -U "$PGUSER_VAL" -d postgres -tAc \
  "SELECT datname FROM pg_database WHERE datistemplate=false AND datname<>'postgres' ORDER BY datname" \
  | tr -d '\r')"
if [ -z "$DBS" ]; then
  log "ERROR no application databases discovered in twenty-db — refusing empty backup"; exit 1
fi
for DB in $DBS; do
  OUT="$DEST/db/$SVC-db-$DB-$STAMP.dump"   # pg custom format (already compressed)
  if in_db pg_dump -U "$PGUSER_VAL" -Fc -d "$DB" > "$OUT"; then
    chmod 600 "$OUT"; log "db ok $DB -> $OUT ($(du -h "$OUT" | cut -f1))"
  else
    log "ERROR pg_dump of '$DB' failed"; rm -f "$OUT"; exit 1
  fi
done

# --- attachments (STORAGE_TYPE=local on the twenty-server PVC) ---
FILES="$DEST/files/$SVC-files-$STAMP.tar.gz"
if kubectl -n "$NS" exec "$SERVER_DEPLOY" -- sh -c "test -d '$TWENTY_STORAGE_PATH' && [ -n \"\$(ls -A '$TWENTY_STORAGE_PATH' 2>/dev/null)\" ]" 2>/dev/null; then
  if kubectl -n "$NS" exec "$SERVER_DEPLOY" -- tar -C "$TWENTY_STORAGE_PATH" -czf - . > "$FILES"; then
    chmod 600 "$FILES"; log "files ok $FILES ($(du -h "$FILES" | cut -f1))"
  else
    log "ERROR attachments tar failed"; rm -f "$FILES"; exit 1
  fi
else
  log "WARN attachments path empty/missing ($TWENTY_STORAGE_PATH) — skipping files layer"
fi

# --- secrets (encrypted; refuse to ever write plaintext) ---
if [ ! -s "$RECIP" ]; then
  log "ERROR age recipient missing ($RECIP) — refusing to write plaintext secrets"; exit 1
fi
SEC="$DEST/secrets/$SVC-secrets-$STAMP.yaml.age"
# shellcheck disable=SC2086 — SECRET_NAMES is an intentional word list
kubectl -n "$NS" get secret $SECRET_NAMES -o yaml | age -r "$(cat "$RECIP")" -o "$SEC"
chmod 600 "$SEC"; log "secrets ok $SEC"

# --- retention ---
prune "$DEST/db" ".sql.gz"; prune "$DEST/db" ".dump"
prune "$DEST/files" ".tar.gz"; prune "$DEST/secrets" ".yaml.age"
log "done (retention=$RETENTION)"
