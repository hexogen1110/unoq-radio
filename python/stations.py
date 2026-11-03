"""Station list: shipped defaults (stations.json) + the user's overlay (stations.user.json).

The overlay only stores what the user changed, so new defaults pulled from git show up
automatically:
    {"added": [{"id", "name", "genre", "url"}, ...], "removed": ["<url of a default>", ...]}

Also runs a background health check (ffmpeg decodes ~1 s of each stream).
"""
import json
import logging
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("stations")
USER_ID_BASE = 10000          # ids of user-added stations start here (defaults use 1..N)
CHECK_TIMEOUT = 15
CHECK_WORKERS = 6


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class StationStore:
    def __init__(self, default_file, user_file):
        self.default_file = default_file
        self.user_file = user_file
        self.defaults = _read(default_file, [])
        ov = _read(user_file, {"added": [], "removed": []})
        if isinstance(ov, list):                      # legacy format: full list → keep only additions
            urls = {s["url"] for s in self.defaults}
            ov = {"added": [s for s in ov if s.get("url") not in urls], "removed": []}
            for i, s in enumerate(ov["added"]):
                s["id"] = USER_ID_BASE + i
        self.added = ov.get("added", [])
        self.removed = set(ov.get("removed", []))
        self.health = {}                              # url -> {"ok": bool, "error": str, "ts": float}
        self.checking = False
        self._lock = threading.Lock()

    # ---------------- List ----------------
    def all(self):
        res = [s for s in self.defaults if s["url"] not in self.removed] + self.added
        return [{**s, "genre": s.get("genre") or "Other", "health": self.health.get(s["url"])} for s in res]

    def get(self, sid):
        return next((s for s in self.all() if s["id"] == sid), None)

    def genres(self):
        seen = []
        for s in self.all():
            if s["genre"] not in seen:
                seen.append(s["genre"])
        return seen

    def _save(self):
        tmp = self.user_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"added": self.added, "removed": sorted(self.removed)},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.user_file)

    def add(self, name, url, genre):
        with self._lock:
            sid = max([s["id"] for s in self.added] + [USER_ID_BASE - 1]) + 1
            self.added.append({"id": sid, "name": name, "genre": genre or "Other", "url": url})
            self.removed.discard(url)
            self._save()

    def delete(self, sid):
        with self._lock:
            if any(s["id"] == sid for s in self.added):
                self.added = [s for s in self.added if s["id"] != sid]
            else:
                d = next((s for s in self.defaults if s["id"] == sid), None)
                if d:
                    self.removed.add(d["url"])
            self._save()

    def restore_defaults(self):
        with self._lock:
            self.removed.clear()
            self._save()

    # ---------------- Health check ----------------
    def check_all(self):
        with self._lock:
            if self.checking:
                return
            self.checking = True
        threading.Thread(target=self._check_all, daemon=True).start()

    def _check_all(self):
        try:
            with ThreadPoolExecutor(CHECK_WORKERS) as ex:
                for s in self.all():
                    ex.submit(self._check_one, s["url"])
        finally:
            self.checking = False
            bad = [u for u, h in self.health.items() if not h["ok"]]
            log.info("station check done: %d ok, %d failed", len(self.health) - len(bad), len(bad))

    def _check_one(self, url):
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-rw_timeout", "8000000", "-i", url, "-t", "1", "-vn", "-f", "null", "-"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT)
            ok = r.returncode == 0
            err = "" if ok else (r.stderr.strip().splitlines() or ["ffmpeg failed"])[-1][-160:]
        except subprocess.TimeoutExpired:
            ok, err = False, "timeout"
        except FileNotFoundError:
            ok, err = False, "ffmpeg not found"
        self.health[url] = {"ok": ok, "error": err, "ts": time.time()}
        if not ok:
            log.warning("station check failed: %s (%s)", url, err)
