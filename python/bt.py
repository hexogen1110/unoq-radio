"""Bluetooth audio device management (wraps bluetoothctl + pactl).

- scan_start(): background scan, collects device name / RSSI / type live
- list_devices(): discovered + paired devices, flagged as audio or not
- connect(mac): pair (NoInputNoOutput agent) → trust → connect → set as default audio output
"""
import logging
import re
import subprocess
import threading
import time

log = logging.getLogger("bt")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x01|\x02")
_DEV_RE = re.compile(r"Device ([0-9A-F:]{17}) (.+)")
_NEW_RE = re.compile(r"\[NEW\] Device ([0-9A-F:]{17}) (.+)")
_DEL_RE = re.compile(r"\[DEL\] Device ([0-9A-F:]{17})")
_RSSI_RE = re.compile(r"\[CHG\] Device ([0-9A-F:]{17}) RSSI: (?:0x[0-9a-f]+ )?\(?(-?\d+)\)?")
_NAME_RE = re.compile(r"\[CHG\] Device ([0-9A-F:]{17}) (?:Name|Alias): (.+)")
_MAC_ONLY = re.compile(r"^([0-9A-F]{2}[-:]){5}[0-9A-F]{2}$")

AUDIO_SINK_UUID = "0000110b"   # A2DP Audio Sink
A2DP_SINK_FULL = "0000110b-0000-1000-8000-00805f9b34fb"


def _clean(s):
    return _ANSI.sub("", s)


def _ctl(*args, timeout=15):
    try:
        r = subprocess.run(["bluetoothctl", *args], capture_output=True, text=True, timeout=timeout)
        return _clean(r.stdout + r.stderr)
    except FileNotFoundError:
        return "bluetoothctl not installed"
    except subprocess.TimeoutExpired:
        return "bluetoothctl timeout"


class _Session:
    """Interactive bluetoothctl: send commands and wait for specific output (pairing needs a resident agent)."""

    def __init__(self):
        self.p = subprocess.Popen(["bluetoothctl"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, bufsize=1)
        self.lines = []
        self._cv = threading.Condition()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        for line in iter(self.p.stdout.readline, ""):
            with self._cv:
                self.lines.append(_clean(line).strip())
                self._cv.notify_all()

    def send(self, cmd):
        self.p.stdin.write(cmd + "\n")
        self.p.stdin.flush()

    def wait_for(self, ok, fail=(), timeout=20):
        """Wait until a line contains any string in ok → True; any in fail → False; timeout → None"""
        start = len(self.lines)
        end = time.time() + timeout
        with self._cv:
            while time.time() < end:
                for l in self.lines[start:]:
                    if any(k in l for k in ok):
                        return True
                    if any(k in l for k in fail):
                        return False
                start = len(self.lines)
                self._cv.wait(timeout=0.5)
        return None

    def close(self):
        try:
            self.send("quit")
            self.p.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.p.kill()


class BtManager:
    def __init__(self):
        self.found = {}          # mac -> {"name", "rssi", "seen"}
        self.meta = {}           # mac -> {"icon", "audio"} (cache of info queries)
        self.scanning = False
        self.busy = ""           # MAC currently being connected
        self._lock = threading.Lock()

    # ---------------- Scan ----------------
    def scan_start(self, seconds=12):
        with self._lock:
            if self.scanning:
                return
            self.scanning = True
        threading.Thread(target=self._scan, args=(seconds,), daemon=True).start()

    def _scan(self, seconds):
        s = None
        try:
            _ctl("power", "on")
            s = _Session()
            s.send("scan on")
            end = time.time() + seconds
            idx = 0
            while time.time() < end:
                time.sleep(0.3)
                for line in s.lines[idx:]:
                    self._parse_scan_line(line)
                idx = len(s.lines)
            s.send("scan off")
        except Exception as e:  # noqa: BLE001
            log.error("scan failed: %s", e)
        finally:
            if s:
                s.close()
            self.scanning = False

    def _parse_scan_line(self, line):
        now = time.time()
        if m := _NEW_RE.search(line):
            d = self.found.setdefault(m.group(1), {"name": "", "rssi": None})
            d["name"] = m.group(2).strip()
            d["seen"] = now
            threading.Thread(target=self._fetch_meta, args=(m.group(1),), daemon=True).start()
        elif m := _RSSI_RE.search(line):
            d = self.found.setdefault(m.group(1), {"name": "", "rssi": None})
            d["rssi"] = int(m.group(2))
            d["seen"] = now
        elif m := _NAME_RE.search(line):
            self.found.setdefault(m.group(1), {"name": "", "rssi": None})["name"] = m.group(2).strip()
        elif m := _DEL_RE.search(line):
            self.found.pop(m.group(1), None)

    def _fetch_meta(self, mac):
        out = _ctl("info", mac)
        icon = (re.search(r"Icon: (\S+)", out) or [None, ""])[1]
        audio = icon.startswith("audio") or "Audio Sink" in out or AUDIO_SINK_UUID in out.lower()
        self.meta[mac] = {"icon": icon, "audio": audio}
        name = (re.search(r"Name: (.+)", out) or [None, ""])[1].strip()
        if name and mac in self.found:
            self.found[mac]["name"] = name

    # ---------------- List / status ----------------
    def info(self, mac):
        out = _ctl("info", mac)
        icon = (re.search(r"Icon: (\S+)", out) or [None, ""])[1]
        return {"mac": mac,
                "name": (re.search(r"Name: (.+)", out) or [None, ""])[1].strip(),
                "paired": "Paired: yes" in out,
                "connected": "Connected: yes" in out,
                "icon": icon,
                "audio": icon.startswith("audio") or "Audio Sink" in out}

    def list_devices(self, current_mac=""):
        paired = {m.group(1): m.group(2).strip() for m in _DEV_RE.finditer(_ctl("devices", "Paired"))}
        macs = set(paired) | set(self.found)
        res = []
        for mac in macs:
            f = self.found.get(mac, {})
            if mac in paired:
                inf = self.info(mac)
            else:
                meta = self.meta.get(mac, {"icon": "", "audio": False})
                inf = {"mac": mac, "name": f.get("name", ""), "paired": False, "connected": False, **meta}
            name = inf["name"] or f.get("name") or paired.get(mac, "")
            if _MAC_ONLY.match(name.replace("-", ":")):
                name = ""                          # unnamed devices: bluetoothctl uses the MAC as the name
            res.append({**inf, "name": name, "rssi": f.get("rssi"),
                        "current": mac == current_mac, "busy": mac == self.busy})
        # sort: current device → paired → audio → strongest signal
        res.sort(key=lambda d: (not d["current"], not d["paired"], not d["audio"],
                                -(d["rssi"] if d["rssi"] is not None else -200)))
        return {"scanning": self.scanning, "busy": self.busy, "devices": res}

    # ---------------- Connect ----------------
    def connect(self, mac):
        self.busy = mac
        steps = []
        s = None
        try:
            _ctl("power", "on")
            s = _Session()
            s.send("agent NoInputNoOutput")      # most speakers use Just Works, no PIN needed
            s.send("default-agent")
            s.wait_for(["Default agent request successful", "Agent registered"], timeout=3)

            if not self.info(mac)["paired"]:
                s.send(f"pair {mac}")
                r = s.wait_for(["Pairing successful", "AlreadyExists"],
                               ["Failed to pair", "not available", "AuthenticationFailed"], timeout=30)
                steps.append(f"pair: {r}")
                if not r:
                    return self._result(mac, False, "Pairing failed: make sure the speaker is in pairing mode (LED blinking fast) and not connected to another phone", steps, s)
            s.send(f"trust {mac}")               # once trusted, the speaker reconnects automatically when powered on
            s.wait_for(["trust succeeded"], timeout=5)

            s.send(f"connect {mac}")
            r = s.wait_for(["Connection successful"], ["Failed to connect", "not available"], timeout=20)
            steps.append(f"connect: {r}")
            if not r:
                return self._result(mac, False, "Connection failed: the speaker may be connected to another device, or needs a power cycle", steps, s)

            sink = self._set_default_sink(mac)
            steps.append(f"sink: {sink}")
            if not sink:
                # ACL is up but A2DP was not set up (common when the speaker auto-reconnects) → connect the A2DP Sink profile explicitly
                s.send(f"connect {mac} {A2DP_SINK_FULL}")
                r = s.wait_for(["Connection successful", "AlreadyConnected"], ["Failed to connect"], timeout=15)
                steps.append(f"connect a2dp: {r}")
                sink = self._set_default_sink(mac)
                steps.append(f"sink(a2dp): {sink}")
            if not sink:
                # last resort: full disconnect and reconnect
                s.send(f"disconnect {mac}")
                s.wait_for(["Successful disconnected", "Disconnection successful"], timeout=8)
                time.sleep(2)
                s.send(f"connect {mac}")
                r = s.wait_for(["Connection successful"], ["Failed to connect", "not available"], timeout=20)
                steps.append(f"reconnect: {r}")
                sink = self._set_default_sink(mac, wait=10)
                steps.append(f"sink(reconnect): {sink}")
            if not sink:
                return self._result(mac, True, "Connected, but no PipeWire Bluetooth sink found (check libspa-0.2-bluetooth / WirePlumber)", steps, s)
            return self._result(mac, True, "", steps, s)
        finally:
            if s:
                s.close()
            self.busy = ""

    def _result(self, mac, ok, msg, steps, s):
        tail = "\n".join(s.lines[-15:]) if s else ""
        return {**self.info(mac), "ok": ok, "message": msg, "log": "\n".join(steps) + "\n---\n" + tail}

    @staticmethod
    def _set_default_sink(mac, wait=8):
        key = "bluez_output." + mac.replace(":", "_")
        end = time.time() + wait
        while time.time() < end:
            try:
                out = subprocess.run(["pactl", "list", "short", "sinks"],
                                     capture_output=True, text=True, timeout=5).stdout
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return None
            for line in out.splitlines():
                cols = line.split("\t")
                if len(cols) > 1 and cols[1].startswith(key):
                    subprocess.run(["pactl", "set-default-sink", cols[1]], timeout=5)
                    return cols[1]
            time.sleep(0.5)
        return None

    def disconnect(self, mac):
        _ctl("disconnect", mac)
        return self.info(mac)

    def remove(self, mac):
        _ctl("remove", mac)
        self.found.pop(mac, None)
        return {"mac": mac, "removed": True}
