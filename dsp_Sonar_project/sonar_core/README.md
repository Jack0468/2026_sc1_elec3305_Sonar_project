# sonar_core

Shared DSP engine for the ELEC3305 Sonar project.

This package provides the validated signal processing functions and real-time audio engine used by **both** the PyQtGraph desktop interface (`rtsonar.py`) and the Flask web application (`flask_app/`). All DSP logic lives here — neither frontend duplicates it.

---

## Structure

```
sonar_core/
├── __init__.py       # Package exports
├── dsp.py            # Core DSP functions (pure NumPy/SciPy, no GUI)
├── engine.py         # SonarEngine — audio I/O threads + signal processing
└── calibration.py    # Auto-calibration script
```

---

## DSP Functions (`dsp.py`)

All functions are stateless and GUI-free. They match the validated implementations in `01_Sonar_Lab_Walkthrough.ipynb`.

| Function | Description |
|----------|-------------|
| `genChirpPulse(Npulse, f0, f1, fs)` | Complex analytic LFM chirp pulse, shape `(Npulse, 1)` |
| `genPulseTrain(pulse, Nrep, Nseg)` | Repeated zero-padded pulse train, length `Nrep × Nseg` |
| `crossCorr(rcv, pulse_a)` | Matched filter via FFT convolution (`fftconvolve`) |
| `findDelay(Xrcv, Nseg)` | Peak sample index within `[0, Nseg)` of the correlation |
| `dist2time(dist, temperature)` | Distance (cm) → round-trip time (s) |
| `time2dist(t, temperature)` | Round-trip time (s) → distance (cm) |

Speed of sound formula used: `v = 331.3 × √(1 + T/273.15)` m/s

**Quick import:**
```python
from sonar_core.dsp import genChirpPulse, crossCorr, dist2time
```

---

## SonarEngine (`engine.py`)

Manages four background threads:

| Thread | Role |
|--------|------|
| `_put_data` | Feeds the pulse train into the playback queue |
| `_play_audio` | Streams the pulse train to the speaker via PyAudio |
| `_record_audio` | Captures microphone input into a queue |
| `_signal_process` | Overlap-and-add matched filtering → output frames |

Processed frames (1-D `numpy` arrays of length `Nplot`) are pushed into a thread-safe `queue.Queue` and retrieved with `get_frame()`.

### Basic usage

```python
from sonar_core.engine import SonarEngine

engine = SonarEngine(
    f0=6000, f1=12000, fs=48000,
    Npulse=500, Nseg=4096, Nrep=24,
    Nplot=200, maxdist=200, temperature=21,
    input_device_index=None,   # None = system default mic
)

engine.start()

try:
    while engine.running:
        frame = engine.get_frame(timeout=1.0)
        if frame is not None:
            # frame is a 1-D float array of length Nplot
            # index 0 = 0 cm, index Nplot-1 = maxdist cm
            print(f"Peak at index {frame.argmax()}")
finally:
    engine.stop()
```

### Constructor parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `f0` | float | — | Chirp start frequency (Hz) |
| `f1` | float | — | Chirp end frequency (Hz) |
| `fs` | float | — | Sampling rate (Hz) |
| `Npulse` | int | — | Chirp pulse length (samples) |
| `Nseg` | int | — | Samples between pulses (sets max range) |
| `Nrep` | int | — | Pulse train repetitions |
| `Nplot` | int | — | Output pixels per sonar line |
| `maxdist` | float | — | Maximum detection range (cm) |
| `temperature` | float | 21 | Ambient temperature (°C) |
| `input_device_index` | int \| None | None | PyAudio input device index |
| `mic_level_callback` | callable \| None | None | Called with peak mic level each chunk |

---

## Auto-Calibration (`calibration.py`)

Plays a wideband chirp, measures the speaker-microphone frequency response, sweeps candidate parameter sets, and writes optimal settings to `calibration_result.json`.

### Run from terminal

```bash
conda activate sonar_env
cd dsp_Sonar_project
python -m sonar_core.calibration
```

You will be prompted to select an audio input device. The script then runs automatically (~10 seconds) and prints the recommended parameters.

### Load calibration results

```python
import json
with open("calibration_result.json") as f:
    params = json.load(f)

engine = SonarEngine(**{k: params[k] for k in
    ["f0","f1","fs","Npulse","Nseg","Nrep","Nplot","maxdist","temperature"]})
```

---

## Running the Tests

```bash
conda activate sonar_env
cd dsp_Sonar_project
python -m pytest tests/ -v
```

19 tests cover all DSP functions (shape, dtype, peak accuracy, round-trip fidelity) and engine construction.

---

## Parameter Tuning Guide

| Goal | Recommended change |
|------|--------------------|
| Better range resolution | Increase bandwidth (`f1 - f0`) |
| Stronger SNR (detect weak echoes) | Increase `Npulse` |
| Longer max range | Increase `Nseg` |
| Quieter operation | Shift `f0/f1` toward 18–20 kHz |
| Faster frame rate | Reduce `Nseg` and `Nrep` |

Use `calibration.py` to auto-select the best `f0/f1/Npulse` for your specific hardware.
