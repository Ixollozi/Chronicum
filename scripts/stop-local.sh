#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="$ROOT/logs/local.pids"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No logs/local.pids"
  exit 0
fi

while read -r pid; do
  [[ -z "$pid" ]] && continue
  if kill "$pid" 2>/dev/null; then
    echo "Stopped PID $pid"
  else
    echo "PID $pid already gone"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
echo "Done."
