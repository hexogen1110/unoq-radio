// UNO Q Radio — MCU side (STM32U585): draw the spectrum on the 13x8 LED matrix.
//
// Called from Linux over the Bridge:
//   bars("<style>|b0,...,b12")   13 band levels 0..8, style 0..3 (see STYLE_* below)
//   idle("")                     idle animation
//   off("")                      blank the matrix (sent before Linux shuts down)
//   version()                    returns SKETCH_VERSION (shown in /api/diag)
//
// Unlit pixels are always fully off; peak-hold caps hang for a moment, then drop one row at a time.
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>

Arduino_LED_Matrix matrix;

const char *SKETCH_VERSION = "unoq-radio sketch v4 (styles: bars/mirror/dots/classic)";

const int COLS = 13, ROWS = 8;
uint8_t frame[COLS * ROWS];          // brightness 0..7 per pixel (3-bit grayscale)

// Display styles — keep in sync with STYLES in python/led.py
enum { STYLE_BARS = 0, STYLE_MIRROR = 1, STYLE_DOTS = 2, STYLE_CLASSIC = 3, STYLE_COUNT };

// Brightness palette
const uint8_t LIT = 5;               // lit segment (VFD-like even brightness)
const uint8_t HOT = 6;               // top segment of a bar
const uint8_t PEAK = 7;              // peak-hold cap / brightest edge

const uint32_t PEAK_HOLD_MS = 700;
const uint32_t PEAK_FALL_MS = 70;

volatile bool hasNew = false;
volatile bool idleMode = true;
volatile bool displayOff = false;    // set by off() before Linux shuts down
volatile int style = STYLE_BARS;
int target[COLS];                    // band levels 0..8 (written by the RPC thread)
int peak[COLS];                      // per-band peak hold, 0..8
uint32_t peakHoldUntil[COLS];
uint32_t lastData = 0;

inline void px(int row, int col, uint8_t v) {
  if (row >= 0 && row < ROWS && col >= 0 && col < COLS) frame[row * COLS + col] = v;
}

// ---- RPC callbacks: parse only, never touch the LEDs here (different thread) ----
void onBars(String s) {
  int start = 0;
  int bar = s.indexOf('|');
  if (bar >= 0) {                    // "<style>|bands[|extra]"; a plain "bands" string still works
    int st = s.substring(0, bar).toInt();
    if (st >= 0 && st < STYLE_COUNT) style = st;
    start = bar + 1;
  }
  int k = 0, from = start;
  for (int i = start; i <= (int)s.length() && k < COLS; i++) {
    if (i == (int)s.length() || s[i] == ',' || s[i] == '|') {
      target[k++] = constrain((int)s.substring(from, i).toInt(), 0, ROWS);
      from = i + 1;
      if (i < (int)s.length() && s[i] == '|') break;
    }
  }
  hasNew = true;
  idleMode = false;
  displayOff = false;
}

void onIdle(String) { idleMode = true; displayOff = false; }

void onOff(String) { displayOff = true; }

String onVersion() { return String(SKETCH_VERSION); }

// ---- Peak hold ----
void updatePeaks(uint32_t now) {
  for (int c = 0; c < COLS; c++) {
    if (target[c] >= peak[c]) { peak[c] = target[c]; peakHoldUntil[c] = now + PEAK_HOLD_MS; }
    else if (now > peakHoldUntil[c] && peak[c] > 0) { peak[c]--; peakHoldUntil[c] = now + PEAK_FALL_MS; }
  }
}

// Style 0 — VFD bars: even-brightness bars, brighter top segment, peak cap above
void drawBars() {
  for (int c = 0; c < COLS; c++) {
    int h = target[c];
    for (int r = 0; r < h; r++) px(ROWS - 1 - r, c, r == h - 1 ? HOT : LIT);
    if (peak[c] > h) px(ROWS - peak[c], c, PEAK);
  }
}

// Style 1 — mirror: each band grows the same number of rows up from row 3 and down from
// row 4 (the centre line), so the top and bottom halves are exact mirror images
void drawMirror() {
  const int top = ROWS / 2 - 1, bottom = ROWS / 2;      // rows 3 and 4
  for (int c = 0; c < COLS; c++) {
    int h = (target[c] + 1) / 2;                        // 0..4 rows per side
    for (int i = 0; i < h; i++) {
      uint8_t v = (i == h - 1) ? HOT : LIT;
      px(top - i, c, v);
      px(bottom + i, c, v);
    }
    int p = (peak[c] + 1) / 2;
    if (p > h) { px(top - (p - 1), c, PEAK); px(bottom + (p - 1), c, PEAK); }
  }
}

// Style 2 — dots: only the current level and the peak cap of each band are lit
void drawDots() {
  for (int c = 0; c < COLS; c++) {
    if (target[c] > 0) px(ROWS - target[c], c, LIT);
    if (peak[c] > 0) px(ROWS - peak[c], c, PEAK);
  }
}

// Style 3 — classic: gradient bars, dim at the bottom and brighter towards the top,
// the top edge of each bar is the brightest pixel, plus a falling peak cap
void drawClassic() {
  for (int c = 0; c < COLS; c++) {
    int h = target[c];
    for (int r = 0; r < h; r++)                         // r = 0 is the bottom row
      px(ROWS - 1 - r, c, r == h - 1 ? PEAK : 1 + (r * 5) / (ROWS - 1));   // 1..6, top edge 7
    if (peak[c] > h) px(ROWS - peak[c], c, PEAK);
  }
}

void drawSpectrum() {
  updatePeaks(millis());
  memset(frame, 0, sizeof(frame));   // background is always fully off
  switch (style) {
    case STYLE_MIRROR:  drawMirror(); break;
    case STYLE_DOTS:    drawDots(); break;
    case STYLE_CLASSIC: drawClassic(); break;
    default:            drawBars(); break;
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
  Bridge.provide("off", onOff);
  Bridge.provide("version", onVersion);
}

void loop() {
  static bool blanked = false;
  if (displayOff) {                  // Linux is shutting down: keep the matrix dark
    if (!blanked) { memset(frame, 0, sizeof(frame)); matrix.draw(frame); blanked = true; }
    delay(20);
    return;
  }
  blanked = false;
  if (hasNew) {
    hasNew = false;
    lastData = millis();
    drawSpectrum();
  } else if (idleMode || millis() - lastData > 2000) {   // no data for 2 s → idle as well
    drawIdle();
  }
  delay(5);
}
