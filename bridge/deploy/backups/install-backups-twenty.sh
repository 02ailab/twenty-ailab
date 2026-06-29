#!/usr/bin/env bash
# Idempotent installer for the Twenty CRM backup (general_docs/BACKUP_STANDARD.md §10).
# Reproducible, file-based — replaces ad-hoc PuTTY commands. Safe to re-run after any
# change to the scripts or cron file. Mirrors install-backups.sh (twenty-bridge) but
# installs the Twenty backup/drill and its OWN cron file so the two installers never
# clobber each other's /etc/cron.d lines.
#
# Usage (on the VPS, after WinSCP-uploading bridge/deploy/backups/):
#   bash /root/twenty-bridge/deploy/backups/install-backups-twenty.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"   # dir this script lives in (the repo copy)
ROOT="/root/backups"; BIN="$ROOT/bin"

echo "==> Directory layout"
mkdir -p "$BIN" "$ROOT/_offsite" "$ROOT/twenty/db" "$ROOT/twenty/files" "$ROOT/twenty/secrets"
chmod -R 700 "$ROOT"

echo "==> Normalize CRLF (WinSCP) + install scripts -> $BIN"
for f in backup-twenty.sh restore-drill-twenty.sh; do
  sed -i 's/\r$//' "$SRC/$f" 2>/dev/null || true
  install -m 700 "$SRC/$f" "$BIN/$f"
done

echo "==> Cron -> /etc/cron.d/saldo-backups-twenty"
sed -i 's/\r$//' "$SRC/saldo-backups-twenty.cron" 2>/dev/null || true
install -m 644 "$SRC/saldo-backups-twenty.cron" /etc/cron.d/saldo-backups-twenty

echo "==> age recipient check"
if [ ! -s "$BIN/age-recipient.txt" ]; then
  echo "  WARNING: $BIN/age-recipient.txt missing — secrets backup will fail-closed."
  echo "  Put the age PUBLIC key there (BACKUP_STANDARD.md §3.3) before the first run."
else
  echo "  OK ($BIN/age-recipient.txt present)"
fi

echo "==> Installed. Active cron:"
cat /etc/cron.d/saldo-backups-twenty
echo "==> First run — VERIFY the 3 live-only assumptions (see header of backup-twenty.sh):"
echo "      $BIN/backup-twenty.sh            # then check /root/backups/twenty/ + backup.log"
echo "      $BIN/restore-drill-twenty.sh     # validate dumps (add --full to load a throwaway DB)"
