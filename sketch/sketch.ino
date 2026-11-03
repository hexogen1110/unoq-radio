// UNO Q Radio — MCU 端（STM32U585）：接收 13 頻段，畫到 8x13 LED 矩陣
// Linux 端呼叫：bars("3,5,8,...")（13 個 0~8）、idle("")
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;

const int COLS = 13, ROWS = 8;
uint8_t frame[COLS * ROWS];

volatile bool hasNew = false;
volatile bool idleMode = true;
int target[COLS];        // RPC 收到的值（RPC 執行緒寫入）
int level[COLS];         // 實際顯示用
int peak[COLS];
uint32_t peakHoldUntil[COLS];
uint32_t lastData = 0;

// ---- RPC callbacks：只解析資料，不直接碰 LED（避免跨執行緒） ----
void onBars(String s) {
  int k = 0, from = 0;
  for (int i = 0; i <= (int)s.length() && k < COLS; i++) {
    if (i == (int)s.length() || s[i] == ',') {
      target[k++] = constrain(s.substring(from, i).toInt(), 0, ROWS);
      from = i + 1;
    }
  }
  hasNew = true;
  idleMode = false;
}

void onIdle(String) { idleMode = true; }

// ---- 繪圖 ----
void drawSpectrum() {
  uint32_t now = millis();
  memset(frame, 0, sizeof(frame));
  for (int c = 0; c < COLS; c++) {
    level[c] = target[c];
    if (level[c] >= peak[c]) { peak[c] = level[c]; peakHoldUntil[c] = now + 400; }
    else if (now > peakHoldUntil[c] && peak[c] > 0) { peak[c]--; peakHoldUntil[c] = now + 60; }

    for (int r = 0; r < level[c]; r++)              // r=0 是最底列
      frame[(ROWS - 1 - r) * COLS + c] = 2 + (r * 4) / ROWS;   // 底部暗、上面亮（2~5）
    if (peak[c] > 0)
      frame[(ROWS - peak[c]) * COLS + c] = 7;       // 峰值點最亮
  }
  matrix.draw(frame);
}

void drawIdle() {
  // 待機：底列一顆光點來回移動
  static uint32_t t0 = 0; static int pos = 0, dir = 1;
  if (millis() - t0 < 90) return;
  t0 = millis();
  memset(frame, 0, sizeof(frame));
  for (int c = 0; c < COLS; c++) frame[(ROWS - 1) * COLS + c] = 1;
  frame[(ROWS - 1) * COLS + pos] = 7;
  if (pos > 0) frame[(ROWS - 1) * COLS + pos - dir] = 3;
  pos += dir;
  if (pos == 0 || pos == COLS - 1) dir = -dir;
  matrix.draw(frame);
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);   // 8 階灰階
  Bridge.begin();
  Bridge.provide("bars", onBars);
  Bridge.provide("idle", onIdle);
}

void loop() {
  if (hasNew) {
    hasNew = false;
    lastData = millis();
    drawSpectrum();
  } else if (idleMode || millis() - lastData > 2000) {   // 2 秒沒資料也回待機
    drawIdle();
  }
  delay(5);
}
