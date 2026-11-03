"""Source: internet radio (ffmpeg decodes HTTP / Icecast / Shoutcast streams)."""
import logging
import re
import subprocess
import threading

from audio_out import CH, SR

log = logging.getLogger("radio")
_TITLE_RE = re.compile(r"StreamTitle\s*[:=]\s*(.*)$")
CHUNK_BYTES = 1024 * CH * 2


class RadioSource:
    name = "radio"

    def __init__(self, out):
        self.out = out
        self.station = None
        self.title = ""
        self.state = "stopped"        # stopped / connecting / playing / error
        self.error = ""
        self._ff = None
        self._thread = None
        self._stop = threading.Event()
        self._last_err = ""

    def play(self, station):
        self.stop()
        self.station, self.title, self.error, self.state = station, "", "", "connecting"
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(station["url"],), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._ff and self._ff.poll() is None:
            self._ff.kill()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=3)
        self._ff = self._thread = None
        if self.state != "error":
            self.state = "stopped"

    def _run(self, url):
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "info",
               "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
               "-i", url, "-vn", "-f", "s16le", "-ac", str(CH), "-ar", str(SR), "pipe:1"]
        try:
            self._ff = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError:
            return self._fail("ffmpeg not found")
        threading.Thread(target=self._read_stderr, args=(self._ff,), daemon=True).start()
        while not self._stop.is_set():
            raw = self._ff.stdout.read(CHUNK_BYTES)
            if not raw:
                if not self._stop.is_set():
                    self._fail("Stream dropped or invalid URL: " + self._last_err[-120:])
                return
            self.state = "playing"
            try:
                self.out.write(raw)
            except OSError:
                return self._fail("Audio output failed (check Bluetooth speaker / PipeWire)")

    def _fail(self, msg):
        log.error(msg)
        self.error, self.state = msg, "error"
        self._stop.set()
        if self._ff and self._ff.poll() is None:
            self._ff.kill()
        self.out.silence()

    def _read_stderr(self, proc):
        for line in iter(proc.stderr.readline, b""):
            s = line.decode("utf-8", "replace").strip()
            if s:
                self._last_err = s
            if m := _TITLE_RE.search(s):
                self.title = m.group(1).strip()

    def status(self):
        return {"state": self.state, "error": self.error, "station": self.station, "title": self.title}
