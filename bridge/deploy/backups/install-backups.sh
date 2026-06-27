#!/usr/bin/env bash
# Idempotent installer for the Saldo backup standard on this VPS
# (general_docs/BACKUP_STANDARD.md). Reproducible, file-based — replaces ad-hoc
# PuTTY commands. Safe to re-run after any change to the scripts or cron file.
#
# Usage (on the VPS, after WinSCP-uploading bridge/deploy/backups/):
#   bash /root/twenty-bridge/deploy/backups/install-backups.sh
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"   # dir this script lives in (the repo copy)
ROOT="/root/backups"; BIN="$ROOT/bin"

echo "==> Directory layout"
mkdir -p "$BIN" "$ROOT/_offsite" "$ROOT/twenty-bridge/db" "$ROOT/twenty-bridge/secrets"
chmod -R 700 "$ROOT"

echo "==> Scripts -> $BIN"
install -m 700 "$SRC/backup-twenty-bridge.sh" "$BIN/backup-twenty-bridge.sh"
install -m 700 "$SRC/offsite-sync.sh"         "$BIN/offsite-sync.sh"

echo "==> Cron -> /etc/cron.d/saldo-backups"
install -m 644 "$SRC/saldo-backups.cron" /etc/cron.d/saldo-backups

echo "==> age recipient check"
if [ ! -s "$BIN/age-recipient.txt" ]; then
  echo "  WARNING: $BIN/age-recipient.txt missing — secrets backup will fail-closed."
  echo "  Put the age PUBLIC key there (BACKUP_STANDARD.md §3.3) before the first run."
else
  echo "  OK ($BIN/age-recipient.txt present)"
fi

echo "==> Installed. Active cron:"
cat /etc/cron.d/saldo-backups
echo "==> Run a backup now to verify: $BIN/backup-twenty-bridge.sh"
