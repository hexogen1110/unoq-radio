#!/usr/bin/env bash
# UNO Q Radio 安裝腳本 — 在 UNO Q 的 Linux 端以一般使用者（預設 arduino）執行
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "== 1. 安裝系統套件 =="
sudo apt update
sudo apt install -y ffmpeg python3-numpy python3-msgpack \
  bluez pipewire pipewire-pulse wireplumber libspa-0.2-bluetooth pulseaudio-utils \
  avahi-daemon

echo "== 2. 啟用藍牙與 mDNS =="
sudo systemctl enable --now bluetooth avahi-daemon
systemctl --user enable --now pipewire pipewire-pulse wireplumber
# 讓使用者服務（PipeWire、收音機）在沒登入時也會啟動
sudo loginctl enable-linger "$USER"
# 無頭模式：WirePlumber 預設只在 active seat 啟用藍牙，關掉 seat monitoring
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
printf 'wireplumber.profiles = {\n  main = {\n    monitor.bluez.seat-monitoring = disabled\n  }\n}\n' \
  > ~/.config/wireplumber/wireplumber.conf.d/80-headless.conf
# LED Bridge：arduino-router.sock 屬於 arduino-router 群組
getent group arduino-router >/dev/null && sudo usermod -aG arduino-router "$USER"

echo "== 3. （選用）安裝 librespot 供 Spotify Connect 使用 =="
if ! command -v librespot >/dev/null; then
  read -r -p "安裝 Spotify Connect（librespot，透過 raspotify 套件）？[y/N] " ans
  if [[ "$ans" =~ ^[Yy]$ ]]; then
    curl -sL https://dtcooper.github.io/raspotify/install.sh | sh
    # 只需要它的 librespot 執行檔；停用 raspotify 自己的服務，改由本程式啟動
    sudo systemctl disable --now raspotify || true
  fi
fi

echo "== 4. 安裝 systemd 使用者服務 =="
mkdir -p ~/.config/systemd/user
sed "s#@APP_DIR@#${APP_DIR}#g" "${APP_DIR}/deploy/unoq-radio.service" > ~/.config/systemd/user/unoq-radio.service
systemctl --user daemon-reload
systemctl --user enable --now unoq-radio

echo
echo "完成！瀏覽器開啟： http://$(hostname).local:8080  或  http://$(hostname -I | awk '{print $1}'):8080"
echo "查看 log： journalctl --user -u unoq-radio -f"
