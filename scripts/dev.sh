#!/bin/bash
# Universal Agent — local dev startup (backend + frontend)
# Usage: bash scripts/dev.sh

set -e
cd "$(dirname "$0")/.."

echo "=== Universal Agent — local development ==="
echo ""

# 1. Backend (agentd on :8765)
echo "[1/2] Starting agentd backend on :8765..."
uv run agent init --output /tmp/ua-dev/universal-agent/profile.json --force 2>/dev/null || true
uv run agent --profile-config /tmp/ua-dev/universal-agent/profile.json serve \
  --port 8765 &
BACKEND_PID=$!
echo "  backend PID: $BACKEND_PID"
echo "  health: http://127.0.0.1:8765/health"

# Wait for backend to be ready
for i in $(seq 1 10); do
  if curl -s http://127.0.0.1:8765/health >/dev/null 2>&1; then
    echo "  backend ready ✓"
    break
  fi
  sleep 0.5
done

# 2. Frontend (Vite dev server on :5173, proxies /api → :8765)
echo ""
echo "[2/2] Starting Vue frontend on :5173..."
echo "  open: http://localhost:5173"
cd web
npm run dev &
FRONTEND_PID=$!
echo "  frontend PID: $FRONTEND_PID"

echo ""
echo "=== Both running. Press Ctrl+C to stop. ==="
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
