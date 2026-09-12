#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "Usage: $0 SOURCE_FILE DESTINATION.conf" >&2
  exit 2
fi

source_file=$1
destination_name=$2
conf_root=/root/dify/docker/nginx/conf.d
container=docker-nginx-1
health_url=https://sysarchn8n.duckdns.org/whieda-platform/health/live

case "$destination_name" in
  *.conf) ;;
  *) echo "Destination must be a .conf filename" >&2; exit 2 ;;
esac
if printf '%s' "$destination_name" | grep -q '[\\/]'; then
  echo "Destination must not contain a path" >&2
  exit 2
fi

test -f "$source_file"
test -d "$conf_root"
test "$(docker inspect -f '{{.State.Running}}' "$container")" = "true"

destination=$conf_root/$destination_name
stamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_root=/root/nginx-backup-$stamp-safe-install
mkdir -m 700 "$backup_root"
had_previous=0
if [ -f "$destination" ]; then
  cp -p "$destination" "$backup_root/$destination_name"
  had_previous=1
fi

rollback() {
  if [ "$had_previous" -eq 1 ]; then
    cp -p "$backup_root/$destination_name" "$destination"
  else
    rm -f "$destination"
  fi
  docker exec "$container" nginx -t >/dev/null 2>&1 || true
  docker exec "$container" nginx -s reload >/dev/null 2>&1 || true
}

cp -p "$source_file" "$destination"
if ! docker exec "$container" nginx -t; then
  rollback
  echo "Rejected: nginx -t failed; previous config restored" >&2
  exit 1
fi

docker exec "$container" nginx -s reload
if ! curl -fsS --max-time 15 "$health_url" >/dev/null; then
  rollback
  echo "Rejected: external Core health failed; previous config restored" >&2
  exit 1
fi

if ! docker exec core-api-1 python scripts/check_telegram_webhook_health.py \
  --binding-id whieda-advisor-bot \
  --expected-url-fragment /whieda-platform/v1/telegram/whieda-advisor-bot/webhook; then
  rollback
  echo "Rejected: Telegram webhook check failed; previous config restored" >&2
  exit 1
fi

echo "Installed safely: $destination"
echo "Backup: $backup_root"
