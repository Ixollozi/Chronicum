#!/usr/bin/env bash
set -euo pipefail

# Daily Postgres backup with 7-day rotation.
# Example cron: 0 3 * * * /path/to/Chronicum/scripts/backup_pg.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-7}"
CONTAINER="${POSTGRES_CONTAINER:-chronicum-postgres-1}"
DB_USER="${POSTGRES_USER:-chronicum}"
DB_NAME="${POSTGRES_DB:-chronicum}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/chronicum_${STAMP}.dump"

if command -v docker >/dev/null 2>&1 && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$CONTAINER"; then
  docker exec -t "$CONTAINER" pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$OUT"
elif command -v pg_dump >/dev/null 2>&1; then
  if [[ -f "$ROOT_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$ROOT_DIR/.env"; set +a
  fi
  : "${DATABASE_URL:?Set DATABASE_URL in .env or run postgres via docker}"
  URL="${DATABASE_URL#postgresql+asyncpg://}"
  URL="${URL#postgresql://}"
  pg_dump --dbname="postgresql://${URL}" -Fc -f "$OUT"
else
  echo "Neither docker postgres container nor local pg_dump found." >&2
  exit 1
fi

echo "Backup written: $OUT"

# Rotation
find "$BACKUP_DIR" -type f -name 'chronicum_*.dump' -mtime +"$KEEP_DAYS" -delete
echo "Rotation done (keep ${KEEP_DAYS} days)."
