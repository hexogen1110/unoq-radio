#!/usr/bin/env bash
# UNO Q Radio read-only diagnostics: run on the board as the arduino user
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
run(){ echo; echo "===== $1 ====="; shift; "$@" 2>&1; }
run id id
run env sh -c 'echo XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR; ls -la $XDG_RUNTIME_DIR | head -30'
run "user units" systemctl --user is-active pipewire pipewire-pulse wireplumber unoq-radio
run "user enabled" systemctl --user is-enabled pipewire pipewire-pulse wireplumber
run linger sh -c 'loginctl show-user $USER -p Linger -p State; ls /var/lib/systemd/linger'
run bluetooth.service systemctl is-active bluetooth
run "pactl info" pactl info
run sinks pactl list short sinks
run cards pactl list short cards
run sink-inputs pactl list short sink-inputs
run "wpctl status" wpctl status
run packages sh -c "dpkg -l | grep -Ei 'pipewire|wireplumber|libspa|bluez|pulseaudio-utils' | awk '{print \$1,\$2,\$3}'"
run "wp config" sh -c 'ls -la ~/.config/wireplumber ~/.config/wireplumber/* /etc/wireplumber /etc/wireplumber/* 2>&1 | head -60'
run "bt show" bluetoothctl show
MAC=$(python3 -c "import json;print(json.load(open(\"$HOME/unoq-radio/python/config.json\")).get(\"bt_mac\",\"\"))" 2>/dev/null)
[ -n "$MAC" ] && run "bt info" bluetoothctl info "$MAC"
run "router sock" sh -c "ls -la /var/run/arduino-router.sock /run/arduino-router*; getent group | grep -Ei 'arduino|router|dialout|bluetooth|audio'"
run "python arduino" python3 -c "import arduino.app_utils as a; print('ok', a.__file__)"
run "journal radio" journalctl --user -u unoq-radio -n 80 --no-pager
run "journal wireplumber" journalctl --user -u wireplumber -n 60 --no-pager
run "journal pipewire" journalctl --user -u pipewire -n 30 --no-pager
run "journal bluetoothd" journalctl -u bluetooth -n 40 --no-pager
