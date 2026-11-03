#!/usr/bin/env python3
"""librespot --onevent hook: POST the event environment variables to the local server."""
import json
import os
import urllib.request

KEYS = ("PLAYER_EVENT", "TRACK_ID", "URI", "NAME", "ARTISTS", "ALBUM", "COVERS", "DURATION_MS",
        "POSITION_MS", "ITEM_TYPE", "SHOW_NAME", "VOLUME", "USER_NAME", "CLIENT_NAME",
        "CLIENT_MODEL_NAME", "UNOQ_EVENT_GEN")
data = {k: os.environ[k] for k in KEYS if k in os.environ}
port = os.environ.get("RADIO_PORT", "8080")
try:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/spotify/event",
                                 data=json.dumps(data).encode(),
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=2).read()
except Exception:  # noqa: BLE001
    pass
