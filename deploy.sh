#!/bin/bash
set -e

PI_HOST="polypi@polypi.local"
REMOTE_DIR="/home/polypi/govbot"

echo "Deploying GovBot to $PI_HOST:$REMOTE_DIR..."

rsync -avz --exclude '.env' \
    --exclude 'data/' \
    --exclude '__pycache__/' \
    --exclude '*.pyc' \
    --exclude '.git/' \
    --exclude 'venv/' \
    ./ "$PI_HOST:$REMOTE_DIR/"

echo "Installing dependencies on Pi..."
ssh "$PI_HOST" "cd $REMOTE_DIR && .venv/bin/pip install -r requirements.txt"

echo "Restarting govbot service..."
ssh "$PI_HOST" "sudo systemctl restart govbot"

echo "Deploy complete. Checking status..."
ssh "$PI_HOST" "sudo systemctl status govbot --no-pager -l"
