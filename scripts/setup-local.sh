#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Chronicum local setup"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

mkdir -p data/files backups logs

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt

cd web
[[ -f .env.local ]] || cp .env.local.example .env.local
npm install
cd "$ROOT"

echo ""
echo "Ensure Postgres (127.0.0.1:5432) and Redis (127.0.0.1:6379) are running."
echo "Create DB:"
echo "  CREATE USER chronicum WITH PASSWORD 'chronicum';"
echo "  CREATE DATABASE chronicum OWNER chronicum;"
echo ""
echo "Optional: ffmpeg, tesseract for video/OCR pipelines."
echo "Next: ./scripts/start-local.sh"
