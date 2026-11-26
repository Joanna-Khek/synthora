#!/bin/sh
# Start MLflow in the background, wait until it answers, then run the bot.
set -e

mkdir -p "$HOME"   # on the volume, so ob's login survives deploys

# Inside the container there's no Obsidian app, so the vault is always plain files.
# (Set here, not in fly.toml: Fly drops environment variables named VAULT*.)
export VAULT__BACKEND=files
export VAULT__PATH=/app/data/vault
export VAULT__NAME=synthora

mlflow server --host :: --port 5000 --workers 1 \
  --backend-store-uri sqlite:////app/data/mlflow.db \
  --artifacts-destination /app/data/mlartifacts &

# The pull API (8.4): its own small process on the same SQLite file.
# It doesn't need MLflow, so it starts straight away.
synthora-api &

# Obsidian Sync: push new notes out and pull your edits in, continuously.
( ob sync --path /app/data/vault --continuous || echo "ob sync stopped" ) &

# The bot connects to MLflow once, at startup, so wait for it (up to 60 s).
# If it never answers, the bot still starts, untraced.
tries=0
until python -c "import urllib.request; urllib.request.urlopen('http://[::1]:5000/health')" 2>/dev/null; do
  tries=$((tries + 1))
  [ "$tries" -ge 60 ] && echo "MLflow didn't start; running the bot without it" && break
  sleep 1
done

# The runner: each minute, turn queued Captures into notes.
# synthora-runner exits in under a second when the queue is empty.
( while true; do
    sleep 60
    synthora-runner || echo "runner failed; trying again in 60 s"
  done ) &

# exec: the bot replaces this shell, so when the bot exits, the machine restarts
exec synthora-bot
