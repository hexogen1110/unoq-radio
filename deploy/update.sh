#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the service.
# Usage (on the UNO Q, as arduino):  bash ~/unoq-radio/deploy/update.sh
# Local settings (python/config.json, python/stations.user.json) are git-ignored and kept.
set -euo pipefail
cd "$(dirname "$0")/.."
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

old=$(git rev-parse HEAD)
git fetch origin
git reset --hard origin/main          # also works after a force push
new=$(git rev-parse HEAD)

if [ "$old" = "$new" ]; then
  echo "Already up to date ($(git log --oneline -1))."
else
  echo "Updated: $(git log --oneline -1)"
  changed=$(git diff --name-only "$old" "$new")
  echo "$changed" | sed 's/^/  /'
  if echo "$changed" | grep -q '^sketch/'; then
    echo "== Sketch changed: uploading to the MCU =="
    arduino-cli compile --fqbn arduino:zephyr:unoq --upload sketch
  fi
  if echo "$changed" | grep -qE '^deploy/(install\.sh|unoq-radio\.service)$'; then
    echo "NOTE: installer/service changed: run 'bash deploy/install.sh' if needed."
  fi
fi

systemctl --user restart unoq-radio
sleep 2
systemctl --user is-active unoq-radio >/dev/null && echo "Service restarted: OK" \
  || { echo "Service failed, recent log:"; journalctl --user -u unoq-radio -n 20 --no-pager; exit 1; }
