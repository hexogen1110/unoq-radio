// UNO Q Radio — MCU side (STM32U585): draw the spectrum on the 13x8 LED matrix
// in the style of a vintage hi-fi VFD (vacuum fluorescent display).
//
// Called from Linux over the Bridge:
//   bars("<style>|b0,...,b12|L,R")   13 band levels 0..8, L/R level 0..13, style 0..3
//   idle("")                         idle animation
//
// VFD look: unlit segments glow faintly ("ghost" segments), lit segments have an even
// brightness, and peak-hold caps hang for a moment before dropping one segment at a time.
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;

// Reported to Linux via Bridge "version" (shown in /api/diag) to confirm which sketch is running
const char *SKETCH_VERSION = "unoq-radio sketch v3 (VFD styles: bars/mirror/dots/meter)";

const int COLS = 13, ROWS = 8;
uint8_t frame[COLS * ROWS];          // brightness 0..7 per pixel (3-bit grayscale)

// Display styles — keep in sync with STYLES in python/led.py
enum { STYLE_BARS = 0, STYLE_MIRROR = 1, STYLE_DOTS = 2, STYLE_METER = 3, STYLE_COUNT };

// Brightness palette
const uint8_t GHOST = 1;             // unlit VFD segment
const uint8_t AXIS = 2;              // centre line / scale ticks
const uint8_t LIT = 5;               // lit segment
const uint8_t HOT = 6;               // top segment / overload zone
const uint8_t PEAK = 7;              // peak-hold cap

const uint32_t PEAK_HOLD_MS = 700;
const uint32_t PEAK_FALL_MS = 70;

volatile bool hasNew = false;
volatile bool idleMode = true;
volatile int style = STYLE_BARS;
int target[COLS];                    // band levels 0..8 (written by the RPC thread)
int vu[2];                           // L/R levels 0..13
int peak[COLS];                      // per-band peak hold, 0..8
uint32_t peakHoldUntil[COLS];
int vuPeak[2];                       // per-channel peak hold, 0..13
uint32_t vuPeakHoldUntil[2];
uint32_t lastData = 0;

inline void px(int row, int col, uint8_t v) {
  if (row >= 0 && row < ROWS && col >= 0 && col < COLS) frame[row * COLS + col] = v;
}

// Parse "a,b,c" into out[0..n-1], each clamped to 0..maxV. Returns the index after the field.
int parseList(const String &s, int start, int *out, int n, int maxV) {
  int k = 0, from = start, i = start;
  for (; i <= (int)s.length(); i++) {
    if (i == (int)s.length() || s[i] == ',' || s[i] == '|') {
      if (k < n) out[k++] = constrain((int)s.substring(from, i).toInt(), 0, maxV);
      from = i + 1;
      if (i == (int)s.length() || s[i] == '|') break;
    }
  }
  return i + 1;
}

// ---- RPC callbacks: parse only, never touch the LEDs here (different thread) ----
void onBars(String s) {
  int pos = 0;
  int bar = s.indexOf('|');
  if (bar >= 0) {                    // "<style>|bands|L,R"; a plain "bands" string still works
    int st = s.substring(0, bar).toInt();
    if (st >= 0 && st < STYLE_COUNT) style = st;
    pos = bar + 1;
  }
  pos = parseList(s, pos, target, COLS, ROWS);
  if (pos < (int)s.length()) parseList(s, pos, vu, 2, COLS);
  hasNew = true;
  idleMode = false;
}

void onIdle(String) { idleMode = true; }

String onVersion() { return String(SKETCH_VERSION); }

// ---- Peak hold (shared) ----
void holdPeak(int value, int &pk, uint32_t &until, uint32_t now) {
  if (value >= pk) { pk = value; until = now + PEAK_HOLD_MS; }
  else if (now > until && pk > 0) { pk--; until = now + PEAK_FALL_MS; }
}

void ghostAll() { memset(frame, GHOST, sizeof(frame)); }

// Style 0 — VFD bars: full ghost grid, even-brightness bars, bright top segment, peak caps
void drawBars() {
  ghostAll();
  for (int c = 0; c < COLS; c++) {
    int h = target[c];
    for (int r = 0; r < h; r++) px(ROWS - 1 - r, c, r == h - 1 ? HOT : LIT);
    if (peak[c] > h) px(ROWS - peak[c], c, PEAK);
  }
}

// Style 1 — mirror: a lit centre line (rows 3 and 4); every band grows the same number
// of segments up and down from it, so the top and bottom halves are exact mirror images.
void drawMirror() {
  const int top = ROWS / 2 - 1, bottom = ROWS / 2;      // rows 3 and 4
  ghostAll();
  for (int c = 0; c < COLS; c++) {
    px(top, c, AXIS); px(bottom, c, AXIS);              // centre line is always visible
    int h = (target[c] + 1) / 2;                        // 0..4 segments per side
    for (int i = 0; i < h; i++) {
      uint8_t v = (i == h - 1) ? HOT : LIT;
      px(top - i, c, v);
      px(bottom + i, c, v);
    }
    int p = (peak[c] + 1) / 2;
    if (p > h) { px(top - (p - 1), c, PEAK); px(bottom + (p - 1), c, PEAK); }
  }
}

// Style 2 — dots: classic "dot mode" — only the current level segment and the peak cap
// are lit, over a ghost grid with a dim baseline
void drawDots() {
  ghostAll();
  for (int c = 0; c < COLS; c++) {
    px(ROWS - 1, c, AXIS);
    if (target[c] > 0) px(ROWS - target[c], c, LIT);
    if (peak[c] > 0) px(ROWS - peak[c], c, PEAK);
  }
}

// Style 3 — L/R level meter: two horizontal bars like a cassette deck, scale ticks on the
// top and bottom rows, the last three columns are the brighter "over" zone
void drawMeter() {
  memset(frame, 0, sizeof(frame));
  for (int c = 0; c < COLS; c++) {
    uint8_t tick = (c % 3 == 0 || c >= COLS - 3) ? AXIS : 0;
    px(0, c, tick);
    px(ROWS - 1, c, tick);
  }
  for (int ch = 0; ch < 2; ch++) {
    int r0 = ch == 0 ? 1 : 4;                           // L: rows 1-3, R: rows 4-6
    for (int c = 0; c < COLS; c++) {
      uint8_t v = c < vu[ch] ? (c >= COLS - 3 ? HOT + 1 : LIT) : GHOST;
      for (int r = r0; r < r0 + 3; r++) px(r, c, v);
    }
    if (vuPeak[ch] > vu[ch] && vuPeak[ch] > 0)
      for (int r = r0; r < r0 + 3; r++) px(r, vuPeak[ch] - 1, PEAK);
  }
}

void drawSpectrum() {
  uint32_t now = millis();
  for (int c = 0; c < COLS; c++) holdPeak(target[c], peak[c], peakHoldUntil[c], now);
  for (int ch = 0; ch < 2; ch++) holdPeak(vu[ch], vuPeak[ch], vuPeakHoldUntil[ch], now);
  switch (style) {
    case STYLE_MIRROR: drawMirror(); break;
    case STYLE_DOTS:   drawDots(); break;
    case STYLE_METER:  drawMeter(); break;
    default:           drawBars(); break;
  }
  matrix.draw(frame);
}

void drawIdle() {
  // Idle: a single dot bouncing along the bottom row
  static uint32_t t0 = 0; static int pos = 0, dir = 1;
  if (millis() - t0 < 90) return;
  t0 = millis();
  memset(frame, 0, sizeof(frame));
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
  Bridge.provide("version", onVersion);
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
