#!/usr/bin/env bash
# Non-destructive restore drill for the Twenty CRM backup (general_docs/BACKUP_STANDARD.md).
# Default mode validates the latest dumps WITHOUT touching live data:
#   - every per-DB .dump parses as a valid pg custom archive (`pg_restore --list`) and
#     contains a non-trivial object count;
#   - the globals .sql.gz passes `gunzip -t` and contains role statements;
#   - the latest age secret bundle exists (decryption needs the operator's offline key).
# With `--full` it additionally restores the newest per-DB dump into a THROWAWAY database
# inside the twenty-db pod and drops it — proving the archive actually loads, still without
# touching the real databases.
#
# Usage (on the VPS):
#   bash /root/backups/bin/restore-drill-twenty.sh           # validate only (safe)
#   bash /root/backups/bin/restore-drill-twenty.sh --full    # + load into throwaway DB
set -euo pipefail

SVC="twenty"; NS="${NS:-twenty}"
ROOT="/root/backups"; DEST="$ROOT/$SVC"; BIN="$ROOT/bin"
LOG="$BIN/backup.log"
DB_DEPLOY="${TWENTY_DB_DEPLOY:-deploy/twenty-db}"
SUPERUSER_SECRET="${TWENTY_SUPERUSER_SECRET:-twenty-db-superuser}"
SUPERUSER_USER_KEY="${SUPERUSER_USER_KEY:-username}"
SUPERUSER_PASS_KEY="${SUPERUSER_PASS_KEY:-password}"
MODE="${1:-}"

log()  { echo "$(date -Iseconds) restore-drill[$SVC] $*" | tee -a "$LOG"; }
fail() { log "FAIL $*"; exit 1; }

# --- globals ---
GLOB="$(ls -1t "$DEST/db/$SVC-db-globals-"*.sql.gz 2>/dev/null | head -n1 || true)"
[ -n "$GLOB" ] || fail "no globals dump found in $DEST/db"
gunzip -t "$GLOB" || fail "globals gzip corrupt: $GLOB"
if ! gunzip -c "$GLOB" | grep -qiE 'CREATE ROLE|ALTER ROLE'; then
  fail "globals dump has no role statements: $GLOB"
fi
log "ok globals $GLOB"

# --- per-DB custom archives ---
# Validate with the pod's pg_restore (version matches the dump; the host may lack pg
# client tools or ship an older pg_restore that rejects a newer custom-format archive).
# `pg_restore --list` reads the archive from stdin and needs no DB connection/creds.
mapfile -t DUMPS < <(ls -1t "$DEST/db/$SVC-db-"*.dump 2>/dev/null || true)
[ "${#DUMPS[@]}" -gt 0 ] || fail "no per-DB .dump archives found in $DEST/db"
NEWEST=""
for D in "${DUMPS[@]}"; do
  COUNT="$(kubectl -n "$NS" exec -i "$DB_DEPLOY" -- pg_restore --list < "$D" 2>/dev/null | grep -cvE '^;|^$' || true)"
  [ "${COUNT:-0}" -gt 0 ] || fail "archive parses to 0 objects (pod pg_restore --list failed or archive corrupt): $D"
  log "ok archive $D ($COUNT objects)"
  [ -z "$NEWEST" ] && NEWEST="$D"
done

# --- secrets bundle present (decryption is operator-only, offline key) ---
SEC="$(ls -1t "$DEST/secrets/$SVC-secrets-"*.yaml.age 2>/dev/null | head -n1 || true)"
[ -n "$SEC" ] || log "WARN no secrets bundle found in $DEST/secrets"
[ -n "$SEC" ] && log "ok secrets bundle present $SEC (decrypt needs offline age key)"

if [ "$MODE" != "--full" ]; then
  log "done (validate-only; pass --full to load newest archive into a throwaway DB)"
  exit 0
fi

# --- full: load newest archive into a throwaway DB, then drop it (non-destructive) ---
b64d() { kubectl -n "$NS" get secret "$SUPERUSER_SECRET" -o "jsonpath={.data.$1}" 2>/dev/null | base64 -d 2>/dev/null; }
PGUSER_VAL="$(b64d "$SUPERUSER_USER_KEY")"; PGPASS_VAL="$(b64d "$SUPERUSER_PASS_KEY")"
[ -n "$PGUSER_VAL" ] && [ -n "$PGPASS_VAL" ] || fail "superuser creds unavailable for --full drill"
in_db() { kubectl -n "$NS" exec -i "$DB_DEPLOY" -- env PGPASSWORD="$PGPASS_VAL" "$@"; }

DRILL_DB="restore_drill_tmp"
log "full: restoring $NEWEST into throwaway db '$DRILL_DB'"
in_db psql -U "$PGUSER_VAL" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DRILL_DB;" >/dev/null
in_db psql -U "$PGUSER_VAL" -d postgres -v ON_ERROR_STOP=1 -c "CREATE DATABASE $DRILL_DB;" >/dev/null
# Restore from stdin into the throwaway db. A production dump loaded into a fresh db normally
# emits NON-FATAL errors (missing owner roles, extension comments) and pg_restore then exits
# non-zero — so success is NOT its exit code. The real check is "did the tables land".
set +e
RESTORE_LOG="$(cat "$NEWEST" | in_db pg_restore -U "$PGUSER_VAL" -d "$DRILL_DB" --no-owner --no-privileges 2>&1)"
set -e
TABLES="$(in_db psql -U "$PGUSER_VAL" -d "$DRILL_DB" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema')" | tr -d '\r')"
ERRS="$(printf '%s\n' "$RESTORE_LOG" | grep -ci 'error:' || true)"
if [ "${TABLES:-0}" -gt 0 ]; then
  log "full: restored into $DRILL_DB ($TABLES tables; $ERRS non-fatal pg_restore errors ignored)"
  in_db psql -U "$PGUSER_VAL" -d postgres -c "DROP DATABASE IF EXISTS $DRILL_DB;" >/dev/null
  log "done (--full drill passed; throwaway db dropped)"
else
  printf '%s\n' "$RESTORE_LOG" | tail -n 20 | sed 's/^/  pg_restore: /' >&2
  in_db psql -U "$PGUSER_VAL" -d postgres -c "DROP DATABASE IF EXISTS $DRILL_DB;" >/dev/null || true
  fail "throwaway restore produced 0 tables (see pg_restore output above)"
fi
