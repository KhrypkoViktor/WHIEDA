#!/bin/sh
# Server-side Core/staging release with a schema gate.
#
#   release_core.sh <core|staging> <revision> <tarball>
#
# The tarball is `git archive <revision> backend/platform-api` produced by
# release_core.ps1. Steps, in order:
#   1. unpack next to the live tree (nothing live is touched yet);
#   2. build api+worker images from the unpacked source;
#   3. run scripts/check_schema_compatibility.py in the NEW image against the
#      target .env — if the database lacks a table the new code needs, stop here:
#      the running containers keep serving the previous release;
#   4. back up and swap src/platform-api, recreate api+worker;
#   5. wait for /health/ready; on failure restore the previous tree and rebuild.
set -eu

target=${1:?core|staging}
revision=${2:?revision}
tarball=${3:?tarball}

case "$target" in
  core)    root=/opt/whieda-platform-core;    compose_dir=$root/src/deploy/core;    health=http://127.0.0.1:8080/health/ready ;;
  staging) root=/opt/whieda-platform-staging; compose_dir=$root/src/deploy/staging; health=http://127.0.0.1:8081/health/ready ;;
  *) echo "target must be core or staging" >&2; exit 2 ;;
esac

test -f "$tarball"
test -d "$compose_dir"
test -f "$compose_dir/docker-compose.yml"
test -f "$compose_dir/.env"

stamp=$(date -u +%Y%m%dT%H%M%SZ)
next=$root/release-next-$revision
backup=$root/backups/$target-$revision-$stamp
live=$root/src/platform-api
previous_revision=$(cat "$root/RELEASE_REVISION" 2>/dev/null || echo unknown)

rm -rf "$next"
mkdir -p "$next" "$backup"
tar -xzf "$tarball" -C "$next"
test -f "$next/backend/platform-api/Dockerfile"

# Build from the unpacked tree by pointing compose at it through a temporary
# override, so the live source is untouched until the gate passes.
override=$compose_dir/docker-compose.release-next.yml
cat > "$override" <<EOF
services:
  api:
    build:
      context: $next/backend/platform-api
  worker:
    build:
      context: $next/backend/platform-api
EOF
cleanup() { rm -f "$override"; }
trap cleanup EXIT

cd "$compose_dir"
docker compose -f docker-compose.yml -f "$override" build api worker

echo "== schema compatibility gate ($target, $revision)"
if ! docker compose -f docker-compose.yml -f "$override" run --rm --no-deps api python scripts/check_schema_compatibility.py; then
  echo "REFUSED: database is missing tables the new code needs; live release untouched" >&2
  rm -rf "$next"
  exit 1
fi

echo "== swapping source tree"
mv "$live" "$backup/platform-api-replaced"
mv "$next/backend/platform-api" "$live"
rmdir "$next/backend" "$next"
printf '%s' "$revision" > "$root/RELEASE_REVISION"

restore() {
  echo "ROLLBACK: $revision never became ready; restoring $previous_revision" >&2
  rm -rf "$live"
  mv "$backup/platform-api-replaced" "$live"
  printf '%s' "$previous_revision" > "$root/RELEASE_REVISION"
  docker compose build api worker
  docker compose up -d --no-deps api worker
}

docker compose up -d --no-deps api worker

echo "== waiting for readiness"
i=0
until curl -fsS --max-time 5 "$health" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    restore
    exit 1
  fi
  sleep 2
done
curl -fsS --max-time 5 "$health"
echo
echo "== released $target=$revision (backup: $backup)"
