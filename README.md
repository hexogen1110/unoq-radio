# UNO Q Radio

Turn an Arduino UNO Q into a standalone network radio. Pick a source in a web page (internet radio or Spotify Connect), play it on a Bluetooth speaker, and watch a live spectrum on the UNO Q LED matrix.

## Architecture

```
 Sources                                Shared output (AudioOutput)
 ┌ RadioSource   ffmpeg ─── PCM ┐        ┌ FFT → LED matrix / web spectrum
 │                              ├─ write ┤
 └ SpotifySource librespot ─ PCM ┘       └ master volume → pacat → PipeWire → Bluetooth speaker
              ▲ --onevent → librespot_event.py → POST /api/spotify/event (track, cover, pause)
 SourceManager: one source at a time, "last action wins"
   - press play in the Spotify app → radio stops, output switches to Spotify
   - pick a station in the web UI  → Spotify is disconnected, output switches to radio
```

To add a new source (for example AirPlay via `shairport-sync --output=stdout`, local music files, a USB stick), write a `src_xxx.py` that feeds PCM (s16le / 44.1 kHz / stereo) to `out.write()` and register it in `SourceManager`.

## Files

| File | Purpose |
|---|---|
| `python/main.py` | Web server (standard library only) and API |
| `python/audio_out.py` | Shared output: master volume, FFT spectrum, LED delay alignment, pacat |
| `python/src_radio.py` | Source: internet radio (ffmpeg) |
| `python/src_spotify.py` | Source: Spotify Connect (librespot) |
| `python/librespot_event.py` | librespot event hook |
| `python/bt.py` | Bluetooth speaker management |
| `python/led.py` | Sends the spectrum to the MCU (picks a Bridge backend automatically) |
| `python/static/index.html` | Mobile-friendly web UI |
| `python/stations.json` | Default station list; edits made in the web UI are saved to `stations.user.json` (git-ignored) |
| `sketch/sketch.ino` | MCU side: LED matrix rendering, 4 display styles |
| `deploy/install.sh` | Installer |
| `deploy/unoq-radio.service` | systemd user service |
| `deploy/fix_headless.sh` | One-off fix for boards installed with an older installer (WirePlumber headless mode, LED socket permission) |
| `deploy/diag.sh` | Read-only diagnostics script (works even when the service is down) |

## Installation (first time)

Run everything below on the UNO Q as the `arduino` user. From a PC use `ssh arduino@<IP>`, or `adb shell` followed by `su - arduino`.

1. **Connect the UNO Q to your home Wi-Fi** (station mode). Streaming needs internet access, so do not use access-point mode.
2. **Get the code**:
   ```bash
   sudo apt install -y git
   git clone https://github.com/hexogen1110/unoq-radio.git ~/unoq-radio
   ```
   If the repository is private, use a GitHub Personal Access Token as the password.
3. **Upload the MCU sketch**:
   ```bash
   arduino-cli compile --fqbn arduino:zephyr:unoq --upload ~/unoq-radio/sketch
   ```
   Alternatively, create an empty app in App Lab, paste the sketch and upload it.
4. **Run the installer** (asks for your sudo password):
   ```bash
   cd ~/unoq-radio && bash deploy/install.sh
   ```
   It installs the packages, enables PipeWire / Bluetooth / mDNS, configures WirePlumber for headless use, adds the user to the `arduino-router` group and registers the systemd service.
5. **Reboot once**: `sudo reboot`. The `arduino-router` group only takes effect after a reboot; the LEDs stay dark until then.
6. On a phone on the same Wi-Fi, open `http://<hostname>.local:8080` (use the IP if your Android phone does not resolve `.local`).
7. In the **Bluetooth audio output** card, put the speaker in pairing mode and press **Scan for devices**. The list updates live with name, signal strength and type (speaker / headphones / other). Press **Pair & connect**.
   - The speaker becomes the PipeWire default output; if something is playing it moves to the new speaker.
   - The last speaker is remembered and reconnected at boot; **Forget** unpairs it.
   - By default only audio devices are listed (Icon `audio-*` or the A2DP Audio Sink UUID); untick the box to see everything.

Optional: the official Bridge Python package `arduino_app_bricks` is not required. Without it, `led.py` talks MsgPack-RPC directly to `/var/run/arduino-router.sock`.

## Everyday use

- **Plug and play**: about a minute after power-on the service is running and the last speaker is reconnected. No login or PC needed.
- **Web UI** (`http://<IP>:8080`): pick stations, volume, spectrum, LED style, Bluetooth, add or remove stations.
- **LED style**: choose **Bars**, **Mirror**, **Peaks** or **Waterfall** under the volume slider. The choice is saved and survives reboots.
- **No sound from the speaker**: make sure it is on and not grabbed by a phone, then press **Connect** on it in the web UI. The app re-establishes the A2DP link if needed.
- **Spotify**: in the Spotify app, open the device list and pick **UNO Q Radio**.

## Updating

After you `git push` changes from your PC, run on the UNO Q:

```bash
cd ~/unoq-radio
git pull
systemctl --user restart unoq-radio
```

| Changed files | Extra step |
|---|---|
| `python/` | the restart above is enough |
| `sketch/` | re-run step 3 to upload the sketch |
| `deploy/install.sh`, `deploy/unoq-radio.service` | re-run `bash deploy/install.sh` |

Stations edited in the web UI live in `stations.user.json` and settings in `config.json`. Both are git-ignored, so `git pull` never conflicts with or overwrites them.

If `git pull` refuses because of local changes (usually from editing files directly on the board), use `git stash` and pull again, or `git checkout -- .` to discard them.

## Service management

```bash
systemctl --user status unoq-radio        # status
systemctl --user restart unoq-radio       # restart
systemctl --user stop unoq-radio          # stop (still starts at boot)
systemctl --user disable --now unoq-radio # stop and disable autostart
journalctl --user -u unoq-radio -f        # follow the log
```

When you come in through `adb shell` instead of SSH, `systemctl --user` may not find the user bus. Run `su - arduino` and then `export XDG_RUNTIME_DIR=/run/user/$(id -u)` first.

## Spotify Connect

- **Requirements**: Spotify **Premium**; phone and UNO Q on the same LAN.
- **How it works**: the open-source [librespot](https://github.com/librespot-org/librespot) turns the UNO Q into a Spotify Connect receiver. **UNO Q Radio** shows up in the app's device list; play, skip and so on happen on the phone, and the web UI shows track, cover art and progress.
- **Install**: `install.sh` asks whether to install it (it uses the raspotify package to get an arm64 librespot and disables raspotify's own service). You can also build it with `cargo install librespot`, which takes a while on the A53.
- **Sign-in**: the first time you pick the device on the phone, Zeroconf authorizes it automatically. Credentials are cached in `~/.cache/unoq-radio/librespot`.
- **Settings**: web UI → Spotify tab → Settings, to rename the device or disable it.
- **Limitations**: you cannot browse Spotify songs from the web UI (there is no official Spotify client for Linux arm64, and the Web API cannot stream audio). **Stop** in the web UI disconnects the Spotify session.

## Tested with

UNO Q (Debian), PipeWire 1.4.2, WirePlumber 0.5.8, BlueZ 5.82, Marshall ACTON III Bluetooth speaker.

## Troubleshooting

Start with the diagnostics: open `http://<IP>:8080/api/diag` in a browser, or, if the service is not running, run this on the PC:
`Get-Content deploy\diag.sh -Raw | ssh arduino@<IP> "tr -d '\r' | bash" > diag.txt`

| Symptom | Cause | Fix |
|---|---|---|
| Web UI says "Connected, but no PipeWire Bluetooth sink found"; `pactl list short sinks` only shows `auto_null` | The speaker auto-reconnected with ACL/AVRCP only; the A2DP (audio) profile was never set up | `bt.py` now connects the A2DP Sink profile explicitly and, if needed, does a full reconnect. Manually: `bluetoothctl connect <MAC> 0000110b-0000-1000-8000-00805f9b34fb` |
| No Bluetooth audio device without a local login | WirePlumber 0.5 only enables Bluetooth on an active logind seat (`monitor.bluez.seat-monitoring`) | `install.sh` writes `~/.config/wireplumber/wireplumber.conf.d/80-headless.conf` to disable it |
| LEDs do not move; log shows `[Errno 13] Permission denied` | `/var/run/arduino-router.sock` is `root:arduino-router 660` | `sudo usermod -aG arduino-router $USER`, then reboot |
| Log shows `No module named 'arduino'` | `arduino_app_bricks` is not installed | Safe to ignore: once the socket permission is fixed, `_RawSocketBackend` is used |
| Only one LED style / style buttons do nothing | Old sketch on the MCU | Re-upload `sketch/sketch.ino` (installation step 3) |

For a board installed with an older version, run `bash deploy/fix_headless.sh && sudo reboot` once.

## Manual tests (debugging)

```bash
# Bluetooth
bluetoothctl            # power on → scan on → pair XX:.. → trust XX:.. → connect XX:..
pactl list short sinks  # should list a bluez_output.XX_XX... sink
# Audio chain
ffmpeg -i https://ice1.somafm.com/groovesalad-128-mp3 -f s16le -ac 2 -ar 44100 - | pacat --raw --format=s16le --rate=44100 --channels=2
# Service
journalctl --user -u unoq-radio -f
```

## Tuning

- **Spectrum ahead of the sound**: web UI → Advanced → spectrum delay; Bluetooth is usually 150–300 ms.
- **Spectrum sensitivity**: `(db + 36) / 36` in `audio_out.py`; a smaller 36 gives more contrast and livelier bars.
- **Frequency range**: `np.logspace(... 60 ... 12000 ...)` in `audio_out.py`.
- **Port**: environment variable `RADIO_PORT`.

## Known limitations

- **Wi-Fi and Bluetooth share 2.4 GHz**: streaming Wi-Fi and A2DP through the same radio can cause dropouts. If you hear gaps, put the UNO Q on **5 GHz** Wi-Fi.
- **App Lab container**: this project runs directly on Debian (systemd user service) because the App Lab Python container does not necessarily have access to PipeWire and Bluetooth.
- **Volume**: software gain in Python. The FFT uses the signal before the volume stage, so turning it down does not shrink the LED spectrum. The speaker's own volume buttons still work.
- **Track titles**: parsed from the ICY `StreamTitle` in ffmpeg's output; only some stations send it.
- **Security**: the web UI has no authentication, so use it on your home LAN only. The diagnostics page `/api/diag` is also unauthenticated and exposes system info, the Bluetooth MAC and logs.
