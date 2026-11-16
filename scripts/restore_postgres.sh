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

if [[ -f "${backup_file}.key" ]]; then
  docker compose run --rm --no-deps -T migrate python -m app.manage restore-key < "${backup_file}.key"
elif ! docker compose run --rm --no-deps -T --entrypoint sh migrate -c \
  'test -s "$SECRET_KEY_FILE" || test -n "$SECRET_KEY"'; then
  echo "Installation key missing. Provide ${backup_file}.key or configure the original SECRET_KEY." >&2
  exit 1
fi
docker compose exec -T postgres sh -c '\
  pg_restore --single-transaction --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$backup_file"
docker compose run --rm --no-deps migrate

echo "Restore completed from: $backup_file"
