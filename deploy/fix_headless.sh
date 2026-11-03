#!/usr/bin/env bash
# 一次性修正：WirePlumber 無頭模式藍牙 + LED Bridge socket 權限
set -euo pipefail
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}

echo "== 1. WirePlumber：關閉 seat monitoring（無頭、沒有本機登入也啟用藍牙/ALSA）=="
mkdir -p ~/.config/wireplumber/wireplumber.conf.d
cat > ~/.config/wireplumber/wireplumber.conf.d/80-headless.conf <<'CONF'
# UNO Q 沒有螢幕登入，logind seat 不是 active → 預設會停用藍牙音訊
wireplumber.profiles = {
  main = {
    monitor.bluez.seat-monitoring = disabled
  }
}
CONF

echo "== 2. LED：加入 arduino-router 群組（/var/run/arduino-router.sock 權限 root:arduino-router 660）=="
sudo usermod -aG arduino-router "$USER"

echo "== 3. 重新開機讓群組生效（user manager 要重開才會拿到新群組）=="
echo "完成。執行： sudo reboot"
