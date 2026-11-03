# UNO Q Radio

在 UNO Q 上用網頁選音源（網路電台 / Spotify Connect），透過藍牙喇叭播放，LED 矩陣同時顯示即時頻譜。

## 音源架構

```
 音源 (Source)                          共用輸出 AudioOutput
 ┌ RadioSource   ffmpeg ─ PCM ┐        ┌ FFT → LED 矩陣 / 網頁頻譜
 │                             ├─ write ┤
 └ SpotifySource librespot ─ PCM ┘      └ 主音量 → pacat → PipeWire → 藍牙喇叭
              ▲ --onevent → librespot_event.py → POST /api/spotify/event（歌名、封面、暫停）
 SourceManager：同時只有一個音源輸出，「最後操作者優先」
   - 手機 Spotify 按播放 → 自動停掉電台、切到 Spotify
   - 網頁點電台 → 中斷 Spotify 連線、切到電台
```

新增音源（例如 AirPlay：`shairport-sync --output=stdout`、本機音樂檔、USB 隨身碟）時，只要寫一個 `src_xxx.py`，把 PCM（s16le / 44.1 kHz / stereo）丟給 `out.write()`，再到 `SourceManager` 註冊即可。


## 目錄

| 檔案 | 說明 |
|---|---|
| `python/main.py` | Web Server（只用標準函式庫）與 API |
| `python/audio_out.py` | 共用輸出：主音量、FFT 頻譜、LED 延遲對齊、pacat |
| `python/src_radio.py` | 音源：網路電台（ffmpeg） |
| `python/src_spotify.py` | 音源：Spotify Connect（librespot） |
| `python/librespot_event.py` | librespot 事件掛鉤 |
| `python/bt.py` | 藍牙喇叭管理 |
| `python/led.py` | 送頻譜到 MCU（自動選 Bridge 後端） |
| `python/static/index.html` | 手機用網頁介面 |
| `python/stations.json` | 電台清單（網頁上也能新增/刪除） |
| `sketch/sketch.ino` | MCU 端 LED 顯示 |
| `deploy/install.sh` | 安裝腳本 |
| `deploy/unoq-radio.service` | systemd 使用者服務 |
| `deploy/fix_headless.sh` | 已安裝舊版的一次性修正（WirePlumber 無頭模式、LED socket 權限） |
| `deploy/diag.sh` | 唯讀診斷腳本（不需要服務在跑） |

## 安裝步驟

1. **UNO Q 連上家裡 Wi-Fi**（Station 模式）。串流電台需要網際網路，所以不要用熱點（AP）模式。
2. 把整個 `unoq-radio` 資料夾複製到 UNO Q，例如 `~/unoq-radio`。
3. **上傳 MCU sketch**：
   ```bash
   arduino-cli compile --fqbn arduino:zephyr:unoq --upload ~/unoq-radio/sketch
   ```
   也可以在 App Lab 建一個空 App，把 sketch 貼進去上傳。
4. **（建議）安裝官方 Bridge Python 套件**，讓非容器的 Python 也能呼叫 MCU：
   ```bash
   pip install --break-system-packages \
     https://github.com/arduino/app-bricks-py/releases/download/release%2F0.8.0/arduino_app_bricks-0.8.0-py3-none-any.whl
   ```
   沒裝的話，`led.py` 會改用 `/var/run/arduino-router.sock` 直接送 MsgPack-RPC。
5. **執行安裝腳本**：
   ```bash
   cd ~/unoq-radio && bash deploy/install.sh
   ```
6. 手機連同一個 Wi-Fi，開 `http://<主機名>.local:8080`（Android 若不支援 `.local`，就用 IP）。
7. 在網頁「藍牙音訊輸出」區塊按 **搜尋藍牙裝置**（喇叭先進入配對模式），清單會即時出現裝置名稱、訊號強度和類型（喇叭／耳機／其他）→ 按 **配對並連線**。
   - 連上後會自動設為 PipeWire 預設輸出；如果正在播放，會自動切到新喇叭。
   - 記住最後一次連線的裝置，開機自動重連；「忘記」會解除配對。
   - 預設只顯示音訊裝置（依 Icon `audio-*` 或 A2DP Audio Sink UUID 判斷），可以取消勾選看全部。

## Spotify Connect

- **需求**：Spotify **Premium**，手機和 UNO Q 在同一個區網。
- **原理**：用開源的 [librespot](https://github.com/librespot-org/librespot) 讓 UNO Q 變成 Spotify Connect 接收端。手機 App 的「裝置」清單會出現 **UNO Q Radio**，選取、播放、切歌都在手機上操作；網頁顯示歌名、封面和進度。
- **安裝**：`install.sh` 會詢問是否安裝（用 raspotify 套件取得 arm64 版 librespot，並停用它自己的服務）。也可以用 `cargo install librespot` 自行編譯，在 A53 上會花比較久。
- **登入**：第一次在手機上選取裝置時，會透過 Zeroconf 自動授權，憑證快取在 `~/.cache/unoq-radio/librespot`。
- **設定**：網頁 → Spotify 分頁 → 設定，可以改裝置名稱或停用。
- **限制**：網頁無法直接選 Spotify 歌曲（官方 Spotify 沒有提供 Linux arm64 用戶端，Web API 也不能串流音訊）。網頁上的「停止」等於中斷 Spotify 連線。

## 已測試環境

UNO Q（Debian）、PipeWire 1.4.2、WirePlumber 0.5.8、BlueZ 5.82，搭配 Marshall ACTON III 藍牙喇叭。

## 疑難排解

先看診斷資料：瀏覽器開 `http://<IP>:8080/api/diag`；或者在服務沒跑時，於 PC 執行
`Get-Content deploy\diag.sh -Raw | ssh arduino@<IP> "tr -d '\r' | bash" > diag.txt`。

| 症狀 | 原因 | 解法 |
|---|---|---|
| 網頁顯示「已連線，但找不到 PipeWire 藍牙 sink」，`pactl list short sinks` 只有 `auto_null` | 喇叭自動回連時只建立了 ACL／AVRCP，沒有建立 A2DP（音訊）profile | `bt.py` 會自動指定 A2DP Sink 重連，必要時整個斷線再連；手動做法：`bluetoothctl connect <MAC> 0000110b-0000-1000-8000-00805f9b34fb` |
| 沒有本機登入時藍牙音訊不出現 | WirePlumber 0.5 預設只在 logind 的 active seat 啟用藍牙（`monitor.bluez.seat-monitoring`） | `install.sh` 會寫入 `~/.config/wireplumber/wireplumber.conf.d/80-headless.conf` 把它關掉 |
| LED 不動，log 出現 `[Errno 13] Permission denied` | `/var/run/arduino-router.sock` 是 `root:arduino-router 660` | `sudo usermod -aG arduino-router $USER`，然後重開機 |
| log 出現 `No module named 'arduino'` | 沒有安裝 `arduino_app_bricks` | 可以忽略：取得 socket 權限後會改用 `_RawSocketBackend` |

舊版已經安裝過的板子，執行一次 `bash deploy/fix_headless.sh && sudo reboot` 即可。

## 手動測試（除錯用）

```bash
# 藍牙
bluetoothctl            # power on → scan on → pair XX:.. → trust XX:.. → connect XX:..
pactl list short sinks  # 應看到 bluez_output.XX_XX... 的 sink
# 音訊鏈
ffmpeg -i https://ice1.somafm.com/groovesalad-128-mp3 -f s16le -ac 2 -ar 44100 - | pacat --raw --format=s16le --rate=44100 --channels=2
# 服務
journalctl --user -u unoq-radio -f
```

## 調整

- **頻譜比聲音早**：網頁 → 進階 → 頻譜延遲，藍牙通常抓 150–300 ms。
- **頻譜靈敏度**：`audio_out.py` 的 `(db + 36) / 36`，36 改小 → 對比更強、跳動更大。
- **頻段範圍**：`audio_out.py` 的 `np.logspace(... 60 ... 12000 ...)`。
- **Port**：環境變數 `RADIO_PORT`。

## 已知限制與注意事項

- **Wi-Fi 與藍牙共用 2.4 GHz**：同一顆無線模組同時串流 Wi-Fi 和 A2DP，可能斷音。如果有斷音，讓 UNO Q 連 **5 GHz** Wi-Fi。
- **App Lab 容器**：本專案設計成直接跑在 Debian 系統層（systemd user service），因為 App Lab 的 Python 容器預設不一定能存取 PipeWire 和藍牙。
- **音量**：在 Python 做軟體增益，FFT 用的是調整音量前的訊號，所以調小聲 LED 頻譜也不會縮。喇叭本身的音量鍵仍然有效。
- **歌名**：解析 ffmpeg 輸出的 ICY `StreamTitle`，只有部分電台會提供。
- **安全性**：網頁沒有登入驗證，只適合在家用區網使用。
- **診斷頁** `/api/diag` 沒有驗證，會列出系統資訊、藍牙 MAC 和 log，只適合在家用區網使用。
