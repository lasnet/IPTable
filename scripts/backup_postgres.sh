#!/usr/bin/env bash
set -euo pipefail
umask 077

backup_dir="${BACKUP_DIR:-backups}"
timestamp="$(date +%Y%m%d_%H%M%S)"
output="${1:-${backup_dir}/iptable_${timestamp}.dump}"

mkdir -p "$(dirname "$output")"

dump_tmp=$(mktemp "${output}.tmp.XXXXXX")
key_tmp=""
trap 'rm -f "$dump_tmp" "${key_tmp:-$dump_tmp}"' EXIT
key_tmp=$(mktemp "${output}.key.tmp.XXXXXX")

docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$dump_tmp"
docker compose run --rm --no-deps -T --entrypoint cat migrate /run/iptable/secret-key > "$key_tmp"
mv "$key_tmp" "${output}.key"
mv "$dump_tmp" "$output"

echo "Backup created: $output and ${output}.key"
