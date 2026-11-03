"""共用音訊輸出：所有音源都把 PCM（s16le / 44.1 kHz / stereo）寫進這裡。

PCM ─┬─ FFT → 13 頻段 → 延遲對齊 → LED 矩陣 / 網頁
     └─ 主音量 → pacat → PipeWire 預設 sink（藍牙喇叭）
"""
import collections
import logging
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("audio")

SR = 44100
CH = 2
FRAME_BYTES = CH * 2
FFT_N = 2048
BANDS = 13
LED_FPS = 30


class AudioOutput:
    def __init__(self, led, volume=60, display_delay_ms=250):
        self.led = led
        self.volume = volume
        self.display_delay_ms = display_delay_ms
        self.levels = [0] * BANDS
        self._pa = None
        self._wlock = threading.Lock()
        self._last_write = 0.0

        edges = np.logspace(np.log10(60), np.log10(12000), BANDS + 1)
        freqs = np.fft.rfftfreq(FFT_N, 1 / SR)
        self._band_idx = []
        for i in range(BANDS):
            idx = np.where((freqs >= edges[i]) & (freqs < edges[i + 1]))[0]
            if len(idx) == 0:
                idx = np.array([np.argmin(abs(freqs - edges[i]))])
            self._band_idx.append(idx)
        self._win = np.hanning(FFT_N)
        self._ring = np.zeros(FFT_N)
        self._smooth = np.zeros(BANDS)
        self._agc = 1e-3
        self._queue = collections.deque()
        threading.Thread(target=self._led_loop, daemon=True).start()

    # ---------------- 輸出 ----------------
    def _open(self):
        cmd = ["pacat", "--playback", "--raw", "--format=s16le", f"--rate={SR}", f"--channels={CH}",
               "--latency-msec=150", "--client-name=UNO-Q-Radio", "--stream-name=player"]
        self._pa = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    def write(self, raw: bytes):
        """寫入 PCM；會阻塞到 pacat 收下（自然以即時速度推進）。"""
        raw = raw[: len(raw) - len(raw) % FRAME_BYTES]
        if not raw:
            return
        pcm = np.frombuffer(raw, np.int16).reshape(-1, CH)
        self._analyze(pcm.mean(axis=1) / 32768.0)
        out = (pcm * ((self.volume / 100.0) ** 2)).astype(np.int16).tobytes()
        with self._wlock:
            for attempt in (1, 2):
                try:
                    if self._pa is None or self._pa.poll() is not None:
                        self._open()
                    self._pa.stdin.write(out)
                    break
                except (BrokenPipeError, ValueError, OSError) as e:
                    log.warning("pacat write failed (%s), reopening", e)
                    self._pa = None
                    if attempt == 2:
                        raise
        self._last_write = time.monotonic()

    def reset(self):
        """切換喇叭 / 切換音源時呼叫：關掉 pacat，下次 write 會重開到新的預設 sink。"""
        with self._wlock:
            if self._pa and self._pa.poll() is None:
                self._pa.kill()
            self._pa = None
        self.silence()

    def silence(self):
        self._queue.clear()
        self._smooth[:] = 0
        self.levels = [0] * BANDS
        self.led.idle()

    def set_volume(self, v):
        self.volume = max(0, min(100, int(v)))

    # ---------------- FFT ----------------
    def _analyze(self, mono):
        n = len(mono)
        if n >= FFT_N:
            self._ring[:] = mono[-FFT_N:]
        else:
            self._ring = np.roll(self._ring, -n)
            self._ring[-n:] = mono
        mag = np.abs(np.fft.rfft(self._ring * self._win))
        band = np.array([mag[i].max() for i in self._band_idx])
        self._agc = max(band.max(), self._agc * 0.995, 1e-3)
        db = 20 * np.log10(band / self._agc + 1e-9)
        h = np.clip((db + 36) / 36 * 8, 0, 8)
        self._smooth = np.maximum(h, self._smooth - 0.6)
        self._queue.append((time.monotonic() + self.display_delay_ms / 1000.0,
                            [int(round(v)) for v in self._smooth]))

    def _led_loop(self):
        idle_sent = True
        while True:
            time.sleep(1.0 / LED_FPS)
            now = time.monotonic()
            latest = None
            while self._queue and self._queue[0][0] <= now:
                latest = self._queue.popleft()[1]
            if latest is not None:
                self.levels = latest
                self.led.bars(latest)
                idle_sent = False
            elif not idle_sent and now - self._last_write > 1.0:   # 暫停 / 斷流 1 秒 → 待機畫面
                self.levels = [0] * BANDS
                self.led.idle()
                idle_sent = True
