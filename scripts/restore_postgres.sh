#!/usr/bin/env bash
set -euo pipefail

backup_file="${1:-}"

if [[ -z "$backup_file" ]]; then
  echo "Usage: CONFIRM_RESTORE=YES scripts/restore_postgres.sh backups/iptable_YYYYmmdd_HHMMSS.dump"
  exit 1
fi

if [[ ! -f "$backup_file" ]]; then
  echo "Backup file not found: $backup_file"
  exit 1
fi

if [[ "${CONFIRM_RESTORE:-}" != "YES" ]]; then
  echo "Restore is destructive. Re-run with CONFIRM_RESTORE=YES."
  exit 1
fi

docker compose cp "$backup_file" postgres:/tmp/iptable_restore.dump
docker compose exec -T postgres sh -c 'pg_restore --clean --if-exists --no-owner -U "$POSTGRES_USER" -d "$POSTGRES_DB" /tmp/iptable_restore.dump'
docker compose exec -T postgres rm -f /tmp/iptable_restore.dump

echo "Restore completed from: $backup_file"
