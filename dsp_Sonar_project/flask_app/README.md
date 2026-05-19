# flask_app — Real-Time Sonar Web Application

Military-themed browser UI for the ELEC3305 sonar system. Streams live matched-filter output and a scrolling waterfall display to any browser via WebSocket, while PyAudio handles audio I/O on the local machine.

---

## Quick Start

```bash
conda activate sonar_env
cd dsp_Sonar_project/flask_app
python app.py
```

Open **http://localhost:5000** in your browser.

> The server must run on the same machine as the speaker and microphone — audio I/O is handled server-side by PyAudio.

---

## Structure

```
flask_app/
├── app.py              # Flask server + SocketIO event handlers
├── sonar_bridge.py     # SonarEngine ↔ WebSocket bridge
├── requirements.txt    # Python dependencies
├── templates/
│   └── index.html      # Military radar UI (green-on-black)
└── static/
    ├── css/style.css   # CRT scanline / phosphor glow theme
    └── js/sonar.js     # WebSocket client + Canvas rendering
```

---

## Architecture

```
Browser (localhost:5000)
    │  WebSocket (socket.io)
    ▼
app.py  ──►  sonar_bridge.py  ──►  SonarEngine (sonar_core)
                │                       │
                │                   PyAudio threads
                │                   (play + record + process)
                │
                └──► socketio.emit("sonar_data", frame)
                         │
                         ▼
                    sonar.js → Canvas waterfall + line plot
```

The bridge uses `socketio.start_background_task` (not a plain thread) so that `socketio.emit` and `socketio.sleep` work correctly inside the SocketIO event loop, ensuring **continuous** frame delivery to the browser.

---

## UI Controls

| Control | Description |
|---------|-------------|
| **F0 / F1** | Chirp start/end frequency (Hz) |
| **NPULSE** | Chirp pulse length (samples) — higher = stronger SNR |
| **NSEG** | Samples between pulses — sets the maximum detectable range |
| **NREP** | Pulse train repetitions — sets waterfall vertical height |
| **MAX DIST** | Detection range ceiling (cm) |
| **TEMP** | Ambient temperature (°C) — corrects speed-of-sound |
| **MIC DEVICE** | Audio input device (populated from the server at page load) |
| **ENGAGE** | Start sonar — waterfall scrolls continuously until disengaged |
| **DISENGAGE** | Stop sonar and audio I/O |
| **CALIBRATE** | Auto-detect optimal f0/f1/Npulse for current hardware |

---

## WebSocket Events

### Client → Server

| Event | Payload | Description |
|-------|---------|-------------|
| `start_sonar` | `{f0, f1, fs, Npulse, Nseg, Nrep, Nplot, maxdist, temperature, input_device_index}` | Start the sonar engine |
| `stop_sonar` | *(none)* | Stop the sonar engine |
| `calibrate` | `{input_device_index, fs}` | Run auto-calibration |

### Server → Client

| Event | Payload | Description |
|-------|---------|-------------|
| `sonar_data` | `{frame, peak_dist, peak_val, mic_level, frame_count}` | Continuous sonar frame (fired every processed line) |
| `status` | `{running: bool}` | Engine start/stop confirmation |
| `calibration_status` | `{status, result?}` | Calibration progress and results |

---

## REST Endpoints

| Route | Method | Description |
|-------|--------|-------------|
| `/` | GET | Serves the sonar UI |
| `/api/devices` | GET | Lists available audio input devices as JSON |
| `/api/calibration` | GET | Returns `calibration_result.json` if it exists |

---

## Dependencies

```
flask>=3.0
flask-socketio>=5.3
numpy
scipy
pyaudio
```

Install into the project environment:

```bash
conda activate sonar_env
pip install flask flask-socketio
```

---

## Display Panels

### Waterfall (`WATERFALL // RANGE HISTORY`)
- Scrolling 2-D heatmap rendered on an HTML5 Canvas
- Each new sonar line is inserted at the **top**, older lines scroll down
- X-axis: distance (cm) from 0 → MAX DIST
- Colour: phosphor green — dark = low amplitude, bright = high amplitude

### Matched Filter (`MATCHED FILTER // LIVE`)
- Real-time amplitude vs. distance line plot
- Green trace with glow effect; grid in dim green
- Peak marks the dominant reflection

### Target Acquisition panel
- **Large distance readout** — distance (cm) of the strongest detected echo
- **PEAK MAG** — normalised peak amplitude
- **MIC LEVEL** — live microphone input bar
- **FRAMES** — total frame count since last ENGAGE

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Waterfall stops updating | Browser lost WebSocket connection | Reload page and re-engage |
| No audio / flat line | Wrong mic device selected | Check MIC DEVICE dropdown; use device listing cell in the notebook |
| Distorted waterfall at start | Synchronisation settling period (~2 s) | Normal — the engine aligns to the pulse train on startup |
| High-pitched chirp audible | f0/f1 in audible range | Use PRESET 4 (15–20 kHz) or run CALIBRATE |
| `RuntimeError: Werkzeug…` | Flask in production mode | Already fixed with `allow_unsafe_werkzeug=True` for local use |
