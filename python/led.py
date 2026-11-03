"""LED matrix output: send the 13 band levels to the MCU.

Backend priority:
1. arduino.app_utils.Bridge (App Lab, or the official package installed from the wheel)
2. MsgPack-RPC notify sent directly to /var/run/arduino-router.sock
3. both fail → log only (handy for development on a PC)
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
            self.notify(func, arg)      # fire-and-forget, faster
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
        try:                      # drain anything the router sends back so the buffer never fills up
            self.s.recv(4096)
        except BlockingIOError:
            pass


class _NullBackend:
    def send(self, func, arg):
        log.debug("LED %s(%s)", func, arg)


STYLES = ["bars", "mirror", "peaks", "waterfall"]   # must match the order in sketch.ino


class Led:
    def __init__(self, style=0):
        self.style = style if 0 <= style < len(STYLES) else 0
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
        """levels: 13 integers in 0..8. Payload: "<style>|v0,v1,...,v12" (style travels with
        every frame, so the MCU never gets out of sync after a reset)."""
        self._send("bars", f"{self.style}|" + ",".join(str(int(v)) for v in levels))

    def set_style(self, style):
        self.style = max(0, min(len(STYLES) - 1, int(style)))

    def idle(self):
        self._send("idle", "")

    def _send(self, func, arg):
        try:
            self.backend.send(func, arg)
        except Exception as e:  # noqa: BLE001
            log.debug("LED send failed: %s", e)
