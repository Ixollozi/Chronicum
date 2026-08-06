#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -d .venv ]]; then
  echo "Missing .venv — run ./scripts/setup-local.sh" >&2
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

set -a
# shellcheck disable=SC1091
source .env
set +a

export STORAGE_PATH="${STORAGE_PATH:-$ROOT/data/files}"
mkdir -p "$STORAGE_PATH" logs

if ! (echo >/dev/tcp/127.0.0.1/5432) >/dev/null 2>&1; then
  echo "PostgreSQL not reachable on 127.0.0.1:5432" >&2
  exit 1
fi
if ! (echo >/dev/tcp/127.0.0.1/6379) >/dev/null 2>&1; then
  echo "Redis not reachable on 127.0.0.1:6379" >&2
  exit 1
fi

cd backend
alembic upgrade head
cd "$ROOT"

: > logs/local.pids

(
  cd backend
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
) >logs/api.out.log 2>logs/api.err.log &
echo $! >> logs/local.pids

(
  cd backend
  arq app.workers.settings.WorkerSettings
) >logs/worker.out.log 2>logs/worker.err.log &
echo $! >> logs/local.pids

(
  cd backend
  python -m app.bot.main
) >logs/bot.out.log 2>logs/bot.err.log &
echo $! >> logs/local.pids

(
  cd web
  npm run dev -- -H 127.0.0.1 -p 3000
) >logs/web.out.log 2>logs/web.err.log &
echo $! >> logs/local.pids

echo "Chronicum started:"
echo "  Web  http://127.0.0.1:3000"
echo "  API  http://127.0.0.1:8000/health"
echo "  Stop ./scripts/stop-local.sh"
