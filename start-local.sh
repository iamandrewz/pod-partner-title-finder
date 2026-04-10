#!/bin/bash
# Start-local.sh — Run both backend and frontend for local development

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "Pod Partner Title Finder — Local Dev"
echo "============================================"

# Start backend
echo "[1/2] Starting backend on port 5003..."
cd "$SCRIPT_DIR/backend"
if [ ! -d "venv" ]; then
  echo "Creating Python venv..."
  python3 -m venv venv
fi
source venv/bin/activate
pip install -q -r requirements.txt 2>/dev/null || true
python app.py &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID"

# Start frontend
echo "[2/2] Starting frontend on port 3102..."
cd "$SCRIPT_DIR/frontend"
if [ ! -d "node_modules" ]; then
  echo "Installing npm packages..."
  npm install --silent
fi
npm run dev &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID"

echo ""
echo "============================================"
echo "Services started:"
echo "  Backend: http://localhost:5003"
echo "  Frontend: http://localhost:3102"
echo ""
echo "Press Ctrl+C to stop both services"
echo "============================================"

# Wait for either process
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
