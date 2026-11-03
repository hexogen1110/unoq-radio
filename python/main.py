"""UNO Q Radio — 本地 Web Server + 音源管理。

音源（Source）：
  - radio   ：網路電台（網頁選台）
  - spotify ：Spotify Connect（手機 Spotify App 選「UNO Q Radio」）
同一時間只有一個音源輸出；「最後操作者優先」——
手機按 Spotify 播放會自動停掉電台；網頁選電台會把正在播的 Spotify 斷開。

啟動：python3 main.py → http://<主機名>.local:8080
"""
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from audio_out import AudioOutput
from bt import BtManager
from led import Led
from src_radio import RadioSource
from src_spotify import SpotifySource

BASE = Path(__file__).resolve().parent
STATIONS_FILE = BASE / "stations.json"
CONFIG_FILE = BASE / "config.json"
PORT = int(os.environ.get("RADIO_PORT", 8080))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")


def load(path, default):
    try:
        return {**default, **json.loads(path.read_text(encoding="utf-8"))} if isinstance(default, dict) \
            else json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


DEFAULT_CONFIG = {
    "volume": 60, "display_delay_ms": 250, "bt_mac": "", "last_station": None,
    "spotify": {"enabled": True, "device_name": "UNO Q Radio", "bitrate": 320, "binary": "librespot"},
}
config = load(CONFIG_FILE, DEFAULT_CONFIG)
stations = load(STATIONS_FILE, [])
_stations_lock = threading.Lock()


def save_config():
    config.update(volume=out.volume, display_delay_ms=out.display_delay_ms)
    save(CONFIG_FILE, config)


# ---------------- 音源管理 ----------------
class SourceManager:
    def __init__(self):
        self.active = None            # "radio" / "spotify" / None
        self._lock = threading.Lock()

    def activate(self, name):
        if self.active == name:
            return
        with self._lock:
            if self.active == name:
                return
            log.info("source: %s -> %s", self.active, name)
            if self.active == "radio":
                radio.stop()
            elif self.active == "spotify":
                spotify.active = False
                spotify.suppress = True
                if spotify.state == "playing":
                    threading.Thread(target=spotify.kick, daemon=True).start()   # 斷開手機的播放
            out.silence()
            self.active = name
            spotify.active = (name == "spotify")

    def stop_all(self):
        with self._lock:
            if self.active == "radio":
                radio.stop()
            elif self.active == "spotify":
                spotify.active = False
                spotify.suppress = True
                threading.Thread(target=spotify.kick, daemon=True).start()
            self.active = None
            out.silence()


out = AudioOutput(Led(), volume=config["volume"], display_delay_ms=config["display_delay_ms"])
mgr = SourceManager()
radio = RadioSource(out)
spotify = SpotifySource(out, config["spotify"], on_activate=mgr.activate,
                        event_script=BASE / "librespot_event.py")
bt = BtManager()


def full_status():
    return {"source": mgr.active, "volume": out.volume, "display_delay_ms": out.display_delay_ms,
            "levels": out.levels, "radio": radio.status(), "spotify": spotify.status(),
            "bt_mac": config.get("bt_mac", "")}



# ---------------- 唯讀診斷 ----------------
DIAG_CMDS = [
    ("id", ["id"]),
    ("env", ["sh", "-c", "echo XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR; echo DBUS=$DBUS_SESSION_BUS_ADDRESS; ls -la $XDG_RUNTIME_DIR 2>&1 | head -30"]),
    ("user units", ["systemctl", "--user", "is-active", "pipewire", "pipewire-pulse", "wireplumber", "unoq-radio"]),
    ("user units enabled", ["systemctl", "--user", "is-enabled", "pipewire", "pipewire-pulse", "wireplumber"]),
    ("linger", ["sh", "-c", "loginctl show-user $USER -p Linger -p State 2>&1; ls /var/lib/systemd/linger 2>&1"]),
    ("bluetooth.service", ["systemctl", "is-active", "bluetooth"]),
    ("pactl info", ["pactl", "info"]),
    ("sinks", ["pactl", "list", "short", "sinks"]),
    ("cards", ["pactl", "list", "short", "cards"]),
    ("sink-inputs", ["pactl", "list", "short", "sink-inputs"]),
    ("wpctl status", ["wpctl", "status"]),
    ("packages", ["sh", "-c", "dpkg -l | grep -Ei 'pipewire|wireplumber|libspa|bluez|pulseaudio-utils' | awk '{print $1,$2,$3}'"]),
    ("wp config", ["sh", "-c", "ls -la ~/.config/wireplumber ~/.config/wireplumber/* /etc/wireplumber /etc/wireplumber/* 2>&1 | head -60"]),
    ("bt show", ["bluetoothctl", "show"]),
    ("bt info", ["sh", "-c", "M=$(python3 -c \"import json;print(json.load(open('config.json')).get('bt_mac',''))\" 2>/dev/null); [ -n \"$M\" ] && bluetoothctl info $M"]),
    ("router sock", ["sh", "-c", "ls -la /var/run/arduino-router.sock /run/arduino-router* 2>&1; getent group | grep -Ei 'arduino|router|dialout|bluetooth|audio' "]),
    ("python arduino", ["python3", "-c", "import arduino.app_utils as a; print('ok', a.__file__)"]),
    ("journal radio", ["journalctl", "--user", "-u", "unoq-radio", "-n", "80", "--no-pager"]),
    ("journal wireplumber", ["journalctl", "--user", "-u", "wireplumber", "-n", "60", "--no-pager"]),
    ("journal pipewire", ["journalctl", "--user", "-u", "pipewire", "-n", "30", "--no-pager"]),
    ("journal bluetoothd", ["sh", "-c", "journalctl -u bluetooth -n 40 --no-pager 2>&1"]),
]


def diag_text():
    import subprocess as sp
    parts = [f"LED backend: {type(out.led.backend).__name__}", f"status: {json.dumps(full_status(), ensure_ascii=False)}"]
    for title, cmd in DIAG_CMDS:
        try:
            r = sp.run(cmd, capture_output=True, text=True, timeout=10, cwd=BASE)
            txt = (r.stdout + r.stderr).strip()
        except Exception as e:  # noqa: BLE001
            txt = f"<{e}>"
        parts.append(f"===== {title} =====\n{txt}")
    return "\n\n".join(parts)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    # ---------- GET ----------
    def do_GET(self):
        p = self.path
        if p in ("/", "/index.html"):
            body = (BASE / "static" / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif p == "/api/status":
            self._json(full_status())
        elif p == "/api/stations":
            self._json(stations)
        elif p == "/api/bt/status":
            mac = config.get("bt_mac")
            self._json(bt.info(mac) if mac else {"mac": "", "connected": False, "paired": False, "name": ""})
        elif p == "/api/diag":
            body = diag_text().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif p == "/api/bt/devices":
            self._json(bt.list_devices(config.get("bt_mac", "")))
        else:
            self._json({"error": "not found"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        try:
            d = self._body()
        except json.JSONDecodeError:
            return self._json({"error": "bad json"}, 400)
        p = self.path

        # ---- 通用播放控制 ----
        if p == "/api/play":                              # 播放電台
            st = next((s for s in stations if s["id"] == d.get("id")), None)
            if not st:
                return self._json({"error": "station not found"}, 404)
            mgr.activate("radio")
            radio.play(st)
            config["last_station"] = st["id"]
            save_config()
            self._json(full_status())
        elif p == "/api/stop":
            mgr.stop_all()
            self._json(full_status())
        elif p == "/api/volume":
            out.set_volume(d.get("volume", out.volume))
            save_config()
            self._json({"volume": out.volume})
        elif p == "/api/delay":
            out.display_delay_ms = max(0, min(1000, int(d.get("ms", 250))))
            save_config()
            self._json({"display_delay_ms": out.display_delay_ms})

        # ---- 電台清單 ----
        elif p == "/api/stations":
            name, url = d.get("name", "").strip(), d.get("url", "").strip()
            if not name or not url.startswith(("http://", "https://")):
                return self._json({"error": "需要名稱與 http(s) 網址"}, 400)
            with _stations_lock:
                new_id = max([s["id"] for s in stations] + [0]) + 1
                stations.append({"id": new_id, "name": name, "url": url})
                save(STATIONS_FILE, stations)
            self._json(stations)
        elif p == "/api/stations/delete":
            with _stations_lock:
                stations[:] = [s for s in stations if s["id"] != d.get("id")]
                save(STATIONS_FILE, stations)
            self._json(stations)

        # ---- Spotify ----
        elif p == "/api/spotify/event":                   # librespot --onevent（只接受本機）
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                return self._json({"error": "forbidden"}, 403)
            spotify.handle_event(d)
            self._json({"ok": True})
        elif p == "/api/spotify/kick":                    # 中斷目前的 Spotify 連線
            if mgr.active == "spotify":
                mgr.stop_all()
            else:
                threading.Thread(target=spotify.kick, daemon=True).start()
            self._json({"ok": True})
        elif p == "/api/spotify/config":                  # 啟用/停用、改裝置名稱
            sc = config["spotify"]
            if "device_name" in d and d["device_name"].strip():
                sc["device_name"] = d["device_name"].strip()[:40]
            if "enabled" in d:
                sc["enabled"] = bool(d["enabled"])
            save_config()
            spotify.shutdown()
            if mgr.active == "spotify":
                mgr.stop_all()
            if sc["enabled"]:
                threading.Thread(target=spotify.kick, daemon=True).start()   # 以新設定重啟
            self._json(spotify.status())

        # ---- 藍牙 ----
        elif p == "/api/bt/scan":
            bt.scan_start(int(d.get("seconds", 12)))
            self._json({"scanning": True})
        elif p == "/api/bt/connect":
            mac = d.get("mac") or config.get("bt_mac")
            if not mac:
                return self._json({"error": "no mac"}, 400)
            if bt.busy:
                return self._json({"error": f"正在連線 {bt.busy}，請稍候"}, 409)
            r = bt.connect(mac)
            if r["ok"]:
                config["bt_mac"] = mac
                save_config()
                out.reset()                               # 下次寫入改走新的預設 sink
            self._json(r)
        elif p == "/api/bt/disconnect":
            mac = d.get("mac") or config.get("bt_mac")
            self._json(bt.disconnect(mac) if mac else {"error": "no mac"})
        elif p == "/api/bt/remove":
            mac = d.get("mac")
            if not mac:
                return self._json({"error": "no mac"}, 400)
            if mac == config.get("bt_mac"):
                config["bt_mac"] = ""
                save_config()
            self._json(bt.remove(mac))
        else:
            self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    os.environ["RADIO_PORT"] = str(PORT)          # 給 librespot_event.py 用
    if config.get("bt_mac"):
        threading.Thread(target=bt.connect, args=(config["bt_mac"],), daemon=True).start()
    spotify.start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("UNO Q Radio on http://0.0.0.0:%d", PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        mgr.stop_all()
        spotify.shutdown()
