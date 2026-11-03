#!/usr/bin/env bash
# Pull the latest code from GitHub and restart the service.
# Usage (on the UNO Q, as arduino):  bash ~/unoq-radio/deploy/update.sh [--sketch]
#   --sketch  force re-uploading the MCU sketch
# Local settings (python/config.json, python/stations.user.json) are git-ignored and kept.
set -euo pipefail
cd "$(dirname "$0")/.."
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

FORCE_SKETCH=0
[ "${1:-}" = "--sketch" ] && FORCE_SKETCH=1

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
  if echo "$changed" | grep -qE '^deploy/(install\.sh|unoq-radio\.service)$'; then
    echo "NOTE: installer/service changed: run 'bash deploy/install.sh' if needed."
  fi
fi

# Upload the sketch whenever its content differs from what was last uploaded from here
# (not only when this pull changed it), or when --sketch is given.
STAMP=~/.cache/unoq-radio/sketch.sha
mkdir -p "$(dirname "$STAMP")"
want=$(sha256sum sketch/sketch.ino | cut -d' ' -f1)
have=$(cat "$STAMP" 2>/dev/null || true)
if [ "$FORCE_SKETCH" = 1 ] || [ "$want" != "$have" ]; then
  echo "== Uploading the sketch to the MCU =="
  if arduino-cli compile --fqbn arduino:zephyr:unoq --upload sketch; then
    echo "$want" > "$STAMP"
    echo "Sketch uploaded."
  else
    echo "!! Sketch upload FAILED (see the output above). The web UI still updates; the LED matrix keeps the old sketch."
  fi
else
  echo "Sketch unchanged since last upload (use --sketch to force)."
fi

systemctl --user restart unoq-radio
sleep 2
systemctl --user is-active unoq-radio >/dev/null && echo "Service restarted: OK" \
  || { echo "Service failed, recent log:"; journalctl --user -u unoq-radio -n 20 --no-pager; exit 1; }
