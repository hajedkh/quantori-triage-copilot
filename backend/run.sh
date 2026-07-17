#!/usr/bin/env bash
# Start the Target Triage Copilot backend on http://localhost:8000
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv…"
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "Installing dependencies (first run only)…"
pip install -q -r requirements.txt

echo "Starting backend on http://localhost:8000"
exec uvicorn app.main:app --reload --port 8000
