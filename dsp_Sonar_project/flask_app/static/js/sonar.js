/**
 * sonar.js
 * ========
 * WebSocket client and Canvas rendering for the Real-Time Sonar System.
 *
 * Handles:
 *  - Socket.IO connection and event handling
 *  - Waterfall (scrolling 2D heatmap) on Canvas
 *  - Matched-filter line plot on Canvas
 *  - UI state management and parameter controls
 */

// ── Globals ──────────────────────────────────────────────────────────────

const socket = io();
let isRunning = false;
let frameTimestamps = [];

// Canvas contexts
const wfCanvas  = document.getElementById('waterfallCanvas');
const wfCtx     = wfCanvas.getContext('2d');
const mfCanvas  = document.getElementById('matchedCanvas');
const mfCtx     = mfCanvas.getContext('2d');

// ── Colour map (jet spectrum) ───────────────────────────────────────────

/**
 * Map a normalised value [0, 1] to a jet-spectrum RGBA colour.
 * 0.0 → deep blue  →  cyan  →  green  →  yellow  →  1.0 → red
 * Provides much more dynamic range than a single-hue phosphor map.
 */
function jetColor(v) {
  v = Math.max(0, Math.min(1, v));
  let r, g, b;
  if (v < 0.125) {
    // deep blue → blue
    r = 0; g = 0;
    b = Math.round(128 + v / 0.125 * 127);
  } else if (v < 0.375) {
    // blue → cyan
    r = 0;
    g = Math.round((v - 0.125) / 0.25 * 255);
    b = 255;
  } else if (v < 0.625) {
    // cyan → yellow
    r = Math.round((v - 0.375) / 0.25 * 255);
    g = 255;
    b = Math.round(255 - (v - 0.375) / 0.25 * 255);
  } else if (v < 0.875) {
    // yellow → red
    r = 255;
    g = Math.round(255 - (v - 0.625) / 0.25 * 255);
    b = 0;
  } else {
    // red → dark red
    r = Math.round(255 - (v - 0.875) / 0.125 * 128);
    g = 0; b = 0;
  }
  return [r, g, b];
}

const colourMap = jetColor;

// ── Canvas setup ─────────────────────────────────────────────────────────

function resizeCanvases() {
  const panels = [
    { canvas: wfCanvas, wrap: wfCanvas.parentElement },
    { canvas: mfCanvas, wrap: mfCanvas.parentElement },
  ];
  for (const { canvas, wrap } of panels) {
    const rect = wrap.getBoundingClientRect();
    canvas.width  = Math.floor(rect.width);
    canvas.height = Math.floor(rect.height);
  }
}

window.addEventListener('resize', resizeCanvases);
window.addEventListener('load', () => {
  resizeCanvases();
  drawGrid(wfCtx, wfCanvas);
  drawGrid(mfCtx, mfCanvas);
  loadDevices();
});

// ── Grid drawing ─────────────────────────────────────────────────────────

function drawGrid(ctx, canvas) {
  ctx.fillStyle = '#020302';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  ctx.strokeStyle = 'rgba(0, 255, 65, 0.06)';
  ctx.lineWidth = 0.5;

  // Vertical grid lines
  const cols = 10;
  for (let i = 1; i < cols; i++) {
    const x = Math.floor(canvas.width * i / cols) + 0.5;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, canvas.height);
    ctx.stroke();
  }

  // Horizontal grid lines
  const rows = 8;
  for (let i = 1; i < rows; i++) {
    const y = Math.floor(canvas.height * i / rows) + 0.5;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(canvas.width, y);
    ctx.stroke();
  }
}

// ── Waterfall rendering ──────────────────────────────────────────────────

let waterfallImg = null;  // ImageData for the full waterfall

function drawWaterfall(frame) {
  const W = wfCanvas.width;
  const H = wfCanvas.height;
  const N = frame.length;

  if (W === 0 || H === 0) return;

  // Initialise the ImageData buffer on first use or resize
  if (!waterfallImg || waterfallImg.width !== W || waterfallImg.height !== H) {
    waterfallImg = wfCtx.createImageData(W, H);
    // Fill fully opaque black
    for (let i = 3; i < waterfallImg.data.length; i += 4) {
      waterfallImg.data[i] = 255;
    }
  }

  // Scroll down: copy rows 0..H-2 to rows 1..H-1
  const rowBytes = W * 4;
  waterfallImg.data.copyWithin(rowBytes, 0, (H - 1) * rowBytes);

  // Write new line at row 0 using jet spectrum
  // The frame is already normalised [0, 1] by the server
  for (let x = 0; x < W; x++) {
    const fi  = Math.floor(x / W * N);
    const v   = Math.max(0, Math.min(1, frame[fi]));
    const [r, g, b] = jetColor(v);
    const idx = x * 4;
    waterfallImg.data[idx]     = r;
    waterfallImg.data[idx + 1] = g;
    waterfallImg.data[idx + 2] = b;
    waterfallImg.data[idx + 3] = 255;
  }

  wfCtx.putImageData(waterfallImg, 0, 0);

  // Overlay axis ticks
  drawWaterfallTicks(W, H);
}

function drawWaterfallTicks(W, H) {
  const maxdist = parseFloat(document.getElementById('paramMaxdist').value) || 200;
  wfCtx.font = 'bold 14px "Orbitron", monospace';
  wfCtx.fillStyle = 'rgba(255, 255, 255, 0.85)';
  wfCtx.textAlign = 'center';
  for (let i = 0; i <= 4; i++) {
    const x = Math.floor(W * i / 4);
    const label = Math.round(maxdist * i / 4);
    // Drop shadow for contrast against waterfall colours
    wfCtx.shadowColor = 'rgba(0,0,0,0.9)';
    wfCtx.shadowBlur = 4;
    wfCtx.fillText(label + '', x, H - 6);
    wfCtx.shadowBlur = 0;
    // Tick line
    wfCtx.fillStyle = 'rgba(255, 255, 255, 0.5)';
    wfCtx.fillRect(x - 0.5, H - 20, 1, 5);
    wfCtx.fillStyle = 'rgba(255, 255, 255, 0.85)';
  }
}

// ── Matched-filter line plot ─────────────────────────────────────────────

function drawMatchedFilter(frame) {
  const W = mfCanvas.width;
  const H = mfCanvas.height;
  const N = frame.length;

  drawGrid(mfCtx, mfCanvas);

  // Axis ticks
  const maxdist = parseFloat(document.getElementById('paramMaxdist').value) || 200;

  // Main trace X ticks
  mfCtx.font = 'bold 14px "Orbitron", monospace';
  mfCtx.fillStyle = 'rgba(0, 255, 65, 0.85)';
  mfCtx.textAlign = 'center';
  for (let i = 0; i <= 4; i++) {
    const x = Math.floor(W * i / 4);
    const label = Math.round(maxdist * i / 4);
    mfCtx.shadowColor = 'rgba(0,0,0,0.8)';
    mfCtx.shadowBlur = 3;
    mfCtx.fillText(label + '', x, H - 4);
    mfCtx.shadowBlur = 0;
    mfCtx.fillRect(x, H - 18, 1, 4);
  }

  // Amplitude labels
  mfCtx.font = 'bold 13px "Orbitron", monospace';
  mfCtx.textAlign = 'right';
  mfCtx.fillStyle = 'rgba(0, 255, 65, 0.85)';
  for (let i = 0; i <= 4; i++) {
    const y = Math.floor(H - (H * i / 4));
    const label = (i / 4).toFixed(2);
    mfCtx.shadowColor = 'rgba(0,0,0,0.8)';
    mfCtx.shadowBlur = 3;
    mfCtx.fillText(label, 36, y - 3);
    mfCtx.shadowBlur = 0;
  }

  // Glow effect — draw a thicker dimmer line behind
  mfCtx.strokeStyle = 'rgba(0, 255, 65, 0.15)';
  mfCtx.lineWidth = 6;
  mfCtx.beginPath();
  for (let i = 0; i < N; i++) {
    const x = (i / (N - 1)) * W;
    const v = Math.max(0, Math.min(1, frame[i]));
    const y = H - v * H;
    if (i === 0) mfCtx.moveTo(x, y);
    else         mfCtx.lineTo(x, y);
  }
  mfCtx.stroke();

  // Main trace
  mfCtx.strokeStyle = '#00ff41';
  mfCtx.lineWidth = 1.5;
  mfCtx.shadowColor = '#00ff41';
  mfCtx.shadowBlur = 4;
  mfCtx.beginPath();
  for (let i = 0; i < N; i++) {
    const x = (i / (N - 1)) * W;
    const v = Math.max(0, Math.min(1, frame[i]));
    const y = H - v * H;
    if (i === 0) mfCtx.moveTo(x, y);
    else         mfCtx.lineTo(x, y);
  }
  mfCtx.stroke();
  mfCtx.shadowBlur = 0;
}

// ── FPS counter ──────────────────────────────────────────────────────────

function updateFPS() {
  const now = performance.now();
  frameTimestamps.push(now);
  // Keep only last 2 seconds of timestamps
  while (frameTimestamps.length > 0 && now - frameTimestamps[0] > 2000) {
    frameTimestamps.shift();
  }
  const fps = Math.round(frameTimestamps.length / 2);
  document.getElementById('fpsCounter').textContent = fps + ' FPS';
}

// ── Socket events ────────────────────────────────────────────────────────

socket.on('connect', () => {
  setLog('CONNECTED TO SERVER');
});

socket.on('disconnect', () => {
  setLog('CONNECTION LOST');
  setRunningState(false);
});

socket.on('status', (data) => {
  setRunningState(data.running);
});

socket.on('sonar_data', (data) => {
  updateFPS();

  // Waterfall — normalise frame for display
  const frame = data.frame;
  drawWaterfall(frame);
  drawMatchedFilter(frame);

  // Distance readout — show '--' when no confident detection
  const distEl = document.getElementById('distReadout');
  const dist = data.peak_dist;
  const newText = dist > 0 ? dist.toFixed(1) : '--';

  // Flash amber on new acquisition
  if (newText !== distEl.textContent && dist > 0) {
    distEl.classList.remove('acquired');
    void distEl.offsetWidth; // reflow to restart animation
    distEl.classList.add('acquired');
    setTimeout(() => distEl.classList.remove('acquired'), 300);
  }
  distEl.textContent = newText;

  document.getElementById('peakMag').textContent =
    data.peak_val.toFixed(3);
  document.getElementById('frameCount').textContent =
    data.frame_count;

  // Mic level bar (0–1 scale, clamp)
  const micPct = Math.min(data.mic_level * 100, 100);
  document.getElementById('micBar').style.width = micPct + '%';
});

socket.on('calibration_status', (data) => {
  if (data.status === 'running') {
    setLog('CALIBRATION IN PROGRESS — STAND BY …');
    document.getElementById('btnCalibrate').disabled = true;
  } else if (data.status === 'complete') {
    setLog('CALIBRATION COMPLETE — PARAMETERS LOADED');
    document.getElementById('btnCalibrate').disabled = false;
    if (data.result) {
      applyCalibration(data.result);
    }
  } else if (data.status === 'error') {
    setLog('CALIBRATION ERROR: ' + data.error);
    document.getElementById('btnCalibrate').disabled = false;
  }
});

// ── UI actions ───────────────────────────────────────────────────────────

function startSonar() {
  const params = gatherParams();
  socket.emit('start_sonar', params);
  setLog('ENGAGING SONAR — f0=' + params.f0 + ' f1=' + params.f1 +
         ' Npulse=' + params.Npulse);
}

function stopSonar() {
  socket.emit('stop_sonar');
  setLog('DISENGAGING SONAR');
}

function runCalibration() {
  const params = gatherParams();
  socket.emit('calibrate', params);
}

function gatherParams() {
  const devSel = document.getElementById('deviceSelect');
  const devVal = devSel.value;
  return {
    f0:      parseFloat(document.getElementById('paramF0').value),
    f1:      parseFloat(document.getElementById('paramF1').value),
    fs:      48000,
    Npulse:  parseInt(document.getElementById('paramNpulse').value),
    Nseg:    parseInt(document.getElementById('paramNseg').value),
    Nrep:    parseInt(document.getElementById('paramNrep').value),
    Nplot:   200,
    maxdist: parseFloat(document.getElementById('paramMaxdist').value),
    temperature: parseFloat(document.getElementById('paramTemp').value),
    input_device_index: devVal === '' ? null : parseInt(devVal),
  };
}

function applyCalibration(result) {
  // Map of result key → element ID
  const fields = [
    ['f0',          'paramF0'],
    ['f1',          'paramF1'],
    ['Npulse',      'paramNpulse'],
    ['Nseg',        'paramNseg'],
    ['Nrep',        'paramNrep'],
    ['maxdist',     'paramMaxdist'],
    ['temperature', 'paramTemp'],
  ];

  for (const [key, elId] of fields) {
    if (result[key] !== undefined && result[key] !== null) {
      const el = document.getElementById(elId);
      if (!el) continue;
      const oldVal = el.value;
      el.value = result[key];
      // Flash amber if value changed
      if (String(result[key]) !== String(oldVal)) {
        el.style.transition = 'background 0.15s ease, color 0.15s ease';
        el.style.background = 'rgba(255,176,0,0.25)';
        el.style.color = '#ffb000';
        setTimeout(() => {
          el.style.background = '';
          el.style.color = '';
        }, 800);
      }
    }
  }
}

// ── Helpers ──────────────────────────────────────────────────────────────

function setRunningState(running) {
  isRunning = running;
  const dot  = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  const btnStart = document.getElementById('btnStart');
  const btnStop  = document.getElementById('btnStop');

  if (running) {
    dot.classList.add('online');
    text.textContent = 'ACTIVE';
    btnStart.disabled = true;
    btnStop.disabled  = false;
  } else {
    dot.classList.remove('online');
    text.textContent = 'OFFLINE';
    btnStart.disabled = false;
    btnStop.disabled  = true;
    frameTimestamps = [];
    document.getElementById('fpsCounter').textContent = '0 FPS';
  }
}

function setLog(msg) {
  document.getElementById('logLine').textContent = msg;
}

async function loadDevices() {
  try {
    const resp = await fetch('/api/devices');
    const devices = await resp.json();
    const sel = document.getElementById('deviceSelect');
    for (const dev of devices) {
      const opt = document.createElement('option');
      opt.value = dev.index;
      opt.textContent = `[${dev.index}] ${dev.name}`;
      sel.appendChild(opt);
    }
  } catch (e) {
    setLog('COULD NOT LOAD AUDIO DEVICES');
  }
}
