"""音源：Spotify Connect 接收端（librespot）。

UNO Q 會出現在 Spotify App 的「裝置」清單；使用者在手機上選它播放。
librespot --backend pipe 把解碼後的 PCM 寫到 stdout → 這裡讀進 AudioOutput。
播放事件（歌名、暫停…）透過 --onevent 腳本 POST 回本機 /api/spotify/event。

需求：Spotify Premium；UNO Q 與手機在同一個區網（mDNS / Zeroconf 探索）。
"""
import logging
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("spotify")
CHUNK_BYTES = 4096


class SpotifySource:
    name = "spotify"

    def __init__(self, out, cfg, on_activate, event_script):
        self.out = out
        self.cfg = cfg                       # {"enabled", "device_name", "bitrate", "binary"}
        self.on_activate = on_activate       # callback(name)：要求管理器切到 Spotify
        self.event_script = event_script
        self.active = False                  # 是否為目前輸出中的音源
        self.suppress = False                # 使用者剛切走：在收到新的 playing 事件前不搶回輸出
        self.proc = None
        self.state = "idle"                  # idle / playing / paused
        self.track = {}
        self.client = ""
        self.user = ""
        self.error = ""
        self.position_ms = 0
        self.position_ts = 0.0
        self._gen = 0                        # 程序世代：忽略舊程序的事件 / 資料
        self._lock = threading.Lock()

    # ---------------- 程序管理 ----------------
    def available(self):
        return shutil.which(self.cfg.get("binary", "librespot")) is not None

    def start(self):
        if not self.cfg.get("enabled", True):
            return
        if not self.available():
            self.error = "找不到 librespot（請參考 README 安裝）"
            log.warning(self.error)
            return
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            self._gen += 1
            gen = self._gen
            cache = Path.home() / ".cache" / "unoq-radio" / "librespot"
            cache.mkdir(parents=True, exist_ok=True)
            cmd = [self.cfg.get("binary", "librespot"),
                   "--name", self.cfg.get("device_name", "UNO Q Radio"),
                   "--device-type", "speaker",
                   "--backend", "pipe",                     # PCM 寫到 stdout
                   "--format", "S16",
                   "--bitrate", str(self.cfg.get("bitrate", 320)),
                   "--initial-volume", "80",
                   "--cache", str(cache),
                   "--disable-audio-cache",
                   "--onevent", str(self.event_script)]
            env = {**os.environ, "UNOQ_EVENT_GEN": str(gen)}
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
            self.error = ""
        threading.Thread(target=self._reader, args=(self.proc, gen), daemon=True).start()
        threading.Thread(target=self._stderr, args=(self.proc,), daemon=True).start()
        log.info("librespot started as '%s'", self.cfg.get("device_name"))

    def kick(self):
        """中斷目前 Spotify 連線（手機端會顯示裝置已離線再重新出現）。"""
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            self.proc = None
        self.state, self.track, self.client = "idle", {}, ""
        time.sleep(0.5)
        self.start()

    def shutdown(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                self.proc.kill()

    def _reader(self, proc, gen):
        while True:
            raw = proc.stdout.read(CHUNK_BYTES)
            if not raw:
                break
            if gen != self._gen:
                break
            if not self.active and not self.suppress:
                # 手機按了播放、但目前輸出是電台 → 要求切到 Spotify（最後操作者優先）
                self.on_activate(self.name)
            if self.active:
                try:
                    self.out.write(raw)
                except OSError:
                    log.error("audio output failed")
        if gen == self._gen and self.cfg.get("enabled", True):
            log.warning("librespot exited, restarting in 3 s")
            self.state = "idle"
            time.sleep(3)
            self.start()

    def _stderr(self, proc):
        for line in iter(proc.stderr.readline, b""):
            s = line.decode("utf-8", "replace").rstrip()
            if " ERROR " in s or "error" in s.lower()[:40]:
                log.warning("librespot: %s", s)

    # ---------------- 事件（由 /api/spotify/event 呼叫） ----------------
    def handle_event(self, ev):
        if str(ev.get("UNOQ_EVENT_GEN", self._gen)) != str(self._gen):
            return
        e = ev.get("PLAYER_EVENT", "")
        if e == "track_changed":
            covers = [c for c in ev.get("COVERS", "").split("\n") if c]
            self.track = {
                "name": ev.get("NAME", ""),
                "artists": ", ".join(a for a in ev.get("ARTISTS", ev.get("SHOW_NAME", "")).split("\n") if a),
                "album": ev.get("ALBUM", ""),
                "cover": covers[0] if covers else "",
                "duration_ms": int(ev.get("DURATION_MS", 0) or 0),
                "uri": ev.get("URI", ""),
            }
        elif e in ("playing", "paused", "seeked", "position_correction"):
            self.position_ms = int(ev.get("POSITION_MS", 0) or 0)
            self.position_ts = time.time()
            if e == "playing":
                self.state = "playing"
                self.suppress = False
                self.on_activate(self.name)
            elif e == "paused":
                self.state = "paused"
        elif e in ("stopped", "session_disconnected"):
            self.state = "idle"
            if e == "session_disconnected":
                self.track, self.client, self.user = {}, "", ""
        elif e == "session_connected":
            self.user = ev.get("USER_NAME", "")
        elif e == "session_client_changed":
            self.client = ev.get("CLIENT_NAME", "") or ev.get("CLIENT_MODEL_NAME", "")

    def status(self):
        pos = self.position_ms
        if self.state == "playing" and self.position_ts:
            pos += int((time.time() - self.position_ts) * 1000)
        return {"enabled": self.cfg.get("enabled", True), "installed": self.available(),
                "running": bool(self.proc and self.proc.poll() is None),
                "device_name": self.cfg.get("device_name", "UNO Q Radio"),
                "state": self.state, "track": self.track, "position_ms": pos,
                "client": self.client, "user": self.user, "error": self.error}
