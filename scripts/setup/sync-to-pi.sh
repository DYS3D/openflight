#!/bin/bash
# usage: PI_HOST=coleman@golfmonitor.local ./scripts/setup/sync-to-pi.sh
set -e
PI_HOST="${PI_HOST:-pi@raspberrypi.local}"
# --delete mirrors ./, so always sync the repo root, never the caller's cwd.
cd "$(dirname "$0")/../.."
rsync -avz --delete \
  --exclude='.venv' --exclude='node_modules' --exclude='__pycache__' \
  --exclude='.git' --exclude='*.pyc' --exclude='ui/dist' --exclude='.pytest_cache' \
  --exclude='session_logs' --exclude='*.pkl' \
  --exclude='uv.lock' --exclude='uv.lock.update-backup' \
  ./ "$PI_HOST:~/openflight/"
echo "Synced to $PI_HOST"
