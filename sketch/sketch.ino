// UNO Q Radio — MCU side (STM32U585): receive 13 band levels and draw them on the 13x8 LED matrix.
// Called from Linux over the Bridge:
//   bars("<style>|3,5,8,...")   13 values in 0..8; style 0..3 (see STYLE_* below)
//   idle("")                    show the idle animation
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;

const int COLS = 13, ROWS = 8;
uint8_t frame[COLS * ROWS];          // brightness 0..7 per pixel (3-bit grayscale)

// Display styles — keep in sync with STYLES in python/led.py
enum { STYLE_BARS = 0, STYLE_MIRROR = 1, STYLE_PEAKS = 2, STYLE_WATERFALL = 3, STYLE_COUNT };

volatile bool hasNew = false;
volatile bool idleMode = true;
volatile int style = STYLE_BARS;
int target[COLS];                    // values from RPC (written by the RPC thread)
int peak[COLS];
uint32_t peakHoldUntil[COLS];
uint32_t lastData = 0;
uint8_t history[ROWS][COLS];         // waterfall buffer, row 0 = newest

inline void px(int row, int col, uint8_t v) {
  if (row >= 0 && row < ROWS && col >= 0 && col < COLS) frame[row * COLS + col] = v;
}

// ---- RPC callbacks: parse only, never touch the LEDs here (different thread) ----
void onBars(String s) {
  int start = 0;
  int bar = s.indexOf('|');
  if (bar >= 0) {                    // "<style>|values"; plain "values" is still accepted
    int st = s.substring(0, bar).toInt();
    if (st >= 0 && st < STYLE_COUNT) style = st;
    start = bar + 1;
  }
  int k = 0, from = start;
  for (int i = start; i <= (int)s.length() && k < COLS; i++) {
    if (i == (int)s.length() || s[i] == ',') {
      target[k++] = constrain(s.substring(from, i).toInt(), 0, ROWS);
      from = i + 1;
    }
  }
  hasNew = true;
  idleMode = false;
}

void onIdle(String) { idleMode = true; }

// ---- Peak tracking shared by all styles ----
void updatePeaks(uint32_t now) {
  for (int c = 0; c < COLS; c++) {
    if (target[c] >= peak[c]) { peak[c] = target[c]; peakHoldUntil[c] = now + 400; }
    else if (now > peakHoldUntil[c] && peak[c] > 0) { peak[c]--; peakHoldUntil[c] = now + 60; }
  }
}

// Style 0: classic bars, dim at the bottom, bright on top, with a falling peak dot
void drawBars() {
  for (int c = 0; c < COLS; c++) {
    for (int r = 0; r < target[c]; r++)                 // r = 0 is the bottom row
      px(ROWS - 1 - r, c, 2 + (r * 4) / ROWS);
    if (peak[c] > 0) px(ROWS - peak[c], c, 7);
  }
}

// Style 1: mirrored bars growing up and down from the center line
void drawMirror() {
  const int half = ROWS / 2;                            // rows 0..3 above, 4..7 below
  for (int c = 0; c < COLS; c++) {
    int h = (target[c] + 1) / 2;                        // 0..4 per side
    for (int i = 0; i < h; i++) {
      uint8_t v = 3 + (i * 4) / half;                   // brighter towards the tips
      px(half - 1 - i, c, v);
      px(half + i, c, v);
    }
    int p = (peak[c] + 1) / 2;
    if (p > 0) { px(half - p, c, 7); px(half - 1 + p, c, 7); }
  }
}

// Style 2: floating dots — a soft dot at the current level and a bright falling peak
void drawPeaks() {
  for (int c = 0; c < COLS; c++) {
    px(ROWS - 1, c, 1);                                 // faint baseline
    if (target[c] > 0) px(ROWS - target[c], c, 3);
    if (target[c] > 1) px(ROWS - target[c] + 1, c, 1);  // short tail below the dot
    if (peak[c] > 0) px(ROWS - peak[c], c, 7);
  }
}

// Style 3: waterfall — each column's level becomes brightness, scrolling downwards
void drawWaterfall(uint32_t now) {
  static uint32_t lastShift = 0;
  if (now - lastShift >= 70) {                          // ~14 rows/s
    lastShift = now;
    memmove(history[1], history[0], sizeof(history) - sizeof(history[0]));
    for (int c = 0; c < COLS; c++) history[0][c] = (target[c] * 7 + ROWS - 1) / ROWS;
  }
  for (int r = 0; r < ROWS; r++)
    for (int c = 0; c < COLS; c++) {
      int v = history[r][c] - r / 3;                    // older rows fade slightly
      px(r, c, v > 0 ? v : 0);
    }
}

void drawSpectrum() {
  uint32_t now = millis();
  updatePeaks(now);
  memset(frame, 0, sizeof(frame));
  switch (style) {
    case STYLE_MIRROR:    drawMirror(); break;
    case STYLE_PEAKS:     drawPeaks(); break;
    case STYLE_WATERFALL: drawWaterfall(now); break;
    default:              drawBars(); break;
  }
  matrix.draw(frame);
}

void drawIdle() {
  // Idle: a single dot bouncing along the bottom row
  static uint32_t t0 = 0; static int pos = 0, dir = 1;
  if (millis() - t0 < 90) return;
  t0 = millis();
  memset(frame, 0, sizeof(frame));
  memset(history, 0, sizeof(history));
  for (int c = 0; c < COLS; c++) frame[(ROWS - 1) * COLS + c] = 1;
  frame[(ROWS - 1) * COLS + pos] = 7;
  if (pos - dir >= 0 && pos - dir < COLS) frame[(ROWS - 1) * COLS + pos - dir] = 3;
  pos += dir;
  if (pos == 0 || pos == COLS - 1) dir = -dir;
  matrix.draw(frame);
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);   // 8 brightness levels
  Bridge.begin();
  Bridge.provide("bars", onBars);
  Bridge.provide("idle", onIdle);
}

void loop() {
  if (hasNew) {
    hasNew = false;
    lastData = millis();
    drawSpectrum();
  } else if (idleMode || millis() - lastData > 2000) {   // no data for 2 s → idle as well
    drawIdle();
  }
  delay(5);
}
