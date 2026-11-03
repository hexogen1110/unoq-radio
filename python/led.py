"""LED 矩陣輸出：把 13 個頻段值送到 MCU。

後端優先順序：
1. arduino.app_utils.Bridge（App Lab 或用 wheel 安裝的官方套件）
2. 直接對 /var/run/arduino-router.sock 送 MsgPack-RPC notify
3. 都失敗 → 只印 log（方便在 PC 上開發測試）
"""
import logging
import socket

log = logging.getLogger("led")
ROUTER_SOCK = "/var/run/arduino-router.sock"


class _AppUtilsBackend:
    def __init__(self):
        from arduino.app_utils import Bridge  # noqa: import inside for optional dep
        self.b = Bridge
        self.notify = getattr(Bridge, "notify", None)

    def send(self, func, arg):
        if self.notify:
            self.notify(func, arg)      # 不等回應，較快
        else:
            self.b.call(func, arg)


class _RawSocketBackend:
    def __init__(self):
        import msgpack
        self.msgpack = msgpack
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(ROUTER_SOCK)
        self.s.setblocking(False)

    def send(self, func, arg):
        # MsgPack-RPC notification: [2, method, params]
        self.s.send(self.msgpack.packb([2, func, [arg]]))
        try:                      # 丟掉 router 可能回來的資料，避免 buffer 塞滿
            self.s.recv(4096)
        except BlockingIOError:
            pass


class _NullBackend:
    def send(self, func, arg):
        log.debug("LED %s(%s)", func, arg)


class Led:
    def __init__(self):
        for cls in (_AppUtilsBackend, _RawSocketBackend):
            try:
                self.backend = cls()
                log.info("LED backend: %s", cls.__name__)
                break
            except Exception as e:  # noqa: BLE001
                log.warning("LED backend %s unavailable: %s", cls.__name__, e)
        else:
            self.backend = _NullBackend()
            log.warning("LED backend: none (log only)")

    def bars(self, levels):
        """levels: 13 個 0~8 整數"""
        self._send("bars", ",".join(str(int(v)) for v in levels))

    def idle(self):
        self._send("idle", "")

    def _send(self, func, arg):
        try:
            self.backend.send(func, arg)
        except Exception as e:  # noqa: BLE001
            log.debug("LED send failed: %s", e)
