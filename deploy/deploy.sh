#!/usr/bin/env bash
# Send the current code to the server and restart it. Run from the repo root on the laptop (Git Bash):
#   FRESHJOBS_HOST=ubuntu@<server ip> deploy/deploy.sh
#
# 1. Builds the frontend here (the server needs no Node).
# 2. Copies the code (not .env, the database, node_modules or the raw H-1B/E-Verify files) over SSH.
# 3. On the server: installs any new Python packages and restarts the backend.
# Run the tests and the build first: deploy only what passed.
set -euo pipefail
HOST="${FRESHJOBS_HOST:?set FRESHJOBS_HOST=ubuntu@<server ip>}"
KEY="${FRESHJOBS_KEY:-$HOME/.ssh/freshjobs_oracle}"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new"

echo "== building the frontend"
(cd frontend && npm run build >/dev/null)

echo "== copying the code"
tar -czf - --exclude=.git --exclude=.env --exclude='backend/.venv' --exclude='frontend/node_modules' \
  --exclude='backend/jobs.db*' --exclude='backend/data/h1b' --exclude='backend/data/everify' \
  --exclude='backend/data/uscis_h1b' --exclude='backend/data/models' --exclude='__pycache__' --exclude='.pytest_cache' \
  . | $SSH "$HOST" 'mkdir -p ~/freshjobs && tar -xzf - -C ~/freshjobs'

echo "== installing packages and restarting"
$SSH "$HOST" 'set -e
cd ~/freshjobs/backend
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart freshjobs-backend
echo "restarted; the backend takes a few minutes to open the database"'
