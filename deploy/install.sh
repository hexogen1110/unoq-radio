#!/usr/bin/env bash
# UNO Q Radio installer — run on the UNO Q Linux side as a normal user (default: arduino)
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1. Install system packages =="
sudo apt update
sudo apt install -y ffmpeg python3-numpy python3-msgpack \
  bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils \
  avahi-daemon

echo "== 2. Enable Bluetooth and mDNS =="
sudo systemctl enable --now bluetooth avahi-daemon
systemctl --user enable --now pipewire pipewire-pulse wireplumber
# Start user services (PipeWire, radio) at boot without a login
sudo loginctl enable-linger "$USER"
# Headless: WirePlumber only enables Bluetooth on an active seat by default, so turn seat monitoring off
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
printf 'wireplumber.profiles = {\n  main = {\n    monitor.bluez.seat-monitoring = disabled\n  }\n}\n' \
  > ~/.config/wireplumber/wireplumber.conf.d/80-headless.conf
# LED Bridge: arduino-router.sock belongs to the arduino-router group (reboot required)
getent group arduino-router >/dev/null && sudo usermod -aG arduino-router "$USER"

echo "== 3. (Optional) Install librespot for Spotify Connect =="
if ! command -v librespot >/dev/null; then
  read -r -p "Install Spotify Connect (librespot via the raspotify package)? [y/N] " ans
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
    # We only need its librespot binary; disable raspotify's own service, this app starts librespot itself
    sudo systemctl disable --now raspotify || true
  fi
fi

echo "== 4. Install the systemd user service =="
mkdir -p ~/.config/systemd/user
sed "s#@APP_DIR@#${APP_DIR}#g" "${APP_DIR}/deploy/unoq-radio.service" > ~/.config/systemd/user/unoq-radio.service
systemctl --user daemon-reload
systemctl --user enable --now unoq-radio

echo
echo "Done! Reboot once (sudo reboot), then open: http://$(hostname).local:8080  or  http://$(hostname -I | awk '{print $1}'):8080"
echo "Logs: journalctl --user -u unoq-radio -f"
