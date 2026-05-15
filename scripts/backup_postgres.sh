#!/usr/bin/env bash
set -euo pipefail

backup_dir="${BACKUP_DIR:-backups}"
timestamp="$(date +%Y%m%d_%H%M%S)"
output="${1:-${backup_dir}/iptable_${timestamp}.dump}"

mkdir -p "$(dirname "$output")"

docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$output"

echo "Backup created: $output"
