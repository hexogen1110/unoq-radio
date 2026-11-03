#!/usr/bin/env bash
# One-off fix for boards installed with an older install.sh:
# WirePlumber headless Bluetooth + LED Bridge socket permission
set -euo pipefail
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

echo "== 1. WirePlumber: disable seat monitoring (enable Bluetooth without a local login) =="
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
cat > ~/.config/wireplumber/wireplumber.conf.d/80-headless.conf <<'CONF'
# The UNO Q has no local login, so the logind seat is never active → Bluetooth audio would be disabled
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
CONF

echo "== 2. LED: join the arduino-router group (/var/run/arduino-router.sock is root:arduino-router 660) =="
sudo usermod -aG arduino-router "$USER"

echo "== 3. Reboot so the new group applies (the systemd user manager only picks it up on restart) =="
echo "Done. Now run: sudo reboot"
