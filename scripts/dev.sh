#!/usr/bin/env bash
# Dev: run postgres (if docker), backend and frontend concurrently
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if command -v docker &>/dev/null; then echo "→ docker compose up postgres redis"; docker compose up -d postgres redis 2>/dev/null || true; fi
echo "→ alembic upgrade"; (cd "$ROOT/backend" && python3.11 -m alembic upgrade head 2>&1 | tail -2)
echo "→ backend http://localhost:8000 (console /console, docs /docs)"
echo "→ frontend http://localhost:5173"
# Use concurrently if available
if command -v concurrently &>/dev/null; then
  concurrently -n backend,frontend -c green,cyan \
    "cd $ROOT/backend && python3.11 -m uvicorn app.main:app --reload --port 8000" \
    "cd $ROOT/frontend && npm run dev"
else
  echo "Install concurrently: npm i -g concurrently — or run in two terminals:"
  echo "  cd backend && python3.11 -m uvicorn app.main:app --reload --port 8000"
  echo "  cd frontend && npm run dev"
fi
