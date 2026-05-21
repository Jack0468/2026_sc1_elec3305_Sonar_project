# ELEC3305 Real-Time Acoustic Sonar — Development Report
### Trial and Error Log: What Was Tested, What Failed, What Worked

---

## 1. Overview

This report documents the full development process of a real-time acoustic sonar system built in Python, running inside a Jupyter notebook and visualised using Bokeh. The system uses a laptop's built-in speaker to transmit a chirp pulse and the built-in microphone to record the echoes. A matched filter (cross-correlation) is then applied to the received signal to extract time-of-arrival information, which is displayed as a scrolling waterfall plot.

The development process was not linear. It involved significant trial and error across multiple stages: signal generation, audio I/O, signal processing, noise suppression, display rendering, and parameter tuning. This report covers each stage in detail — what was attempted, what broke, what was fixed, and what was ultimately kept in the final system.

The codebase is split across two files:
- `rtsonar.py` — the real-time sonar engine (audio threads, signal processing, display)
- `TimeDomain-RealTime-Sonar.ipynb` — the Jupyter notebook containing the user-defined DSP functions and the parameter configuration cells

---

## 2. System Architecture

Before discussing what was changed, it helps to understand what the system does at a high level.

The sonar works as follows:

1. A chirp pulse (Linear Frequency Modulated, or LFM) is generated — a sinusoid that sweeps from frequency `f0` to `f1` over `Npulse` samples.
2. A pulse train is constructed by repeating the pulse `Nrep` times, each separated by `Nseg` zero-padded samples.
3. The pulse train is played through the speaker via PyAudio.
4. The microphone simultaneously records the room's acoustic response.
5. A matched filter is applied to the recorded signal by cross-correlating it with the transmitted pulse template.
6. The output of the cross-correlation gives peaks at time delays corresponding to the distances of reflecting objects.
7. These peaks are extracted, interpolated, and rendered as one horizontal line on a waterfall plot. Each new line is added to the top of the waterfall, and old lines scroll downward, forming a 2D time-vs-distance image.

Five threads run concurrently:
- `put_data` — feeds the pulse train into the playback queue
- `play_audio` — writes audio data to the speaker stream
- `record_audio` — reads from the microphone and pushes data to the processing queue
- `signal_process` — applies matched filtering and queues display data
- `image_update` — renders the waterfall plot in the Bokeh figure

A `stop_flag` (threading.Event) is returned to the user and can be set to cleanly shut down all threads.

---

## 3. Initial Issues: NumPy Namespace Collision

### The Problem

The codebase uses `from numpy import *`, which is common in scientific Python code because it brings all NumPy functions into the global namespace (e.g. `zeros`, `r_`, `abs`). However, this has a critical side effect: it **shadows Python's built-in `max` and `min` functions** with NumPy's versions.

NumPy's `max` and `min` behave differently from Python's built-ins. When called on a plain Python list, NumPy's versions can raise `AxisError` or return unexpected results. This caused a crash during calibration:

```
AxisError: axis 12288 is out of bounds for array of dimension 1
```

This error occurred in `calibrate_sonar` when `min(ptr + chunk, len(ptrain))` was called — Python's `min` had been replaced by NumPy's `min`, which interpreted the two integers as an array and a reduction axis, not two values to compare.

### The Fix

The solution was to save references to the built-in functions before the wildcard import overwrites them:

```python
builtins_max, builtins_min = max, min
from numpy import *
```

After this, all comparisons that needed true Python min/max used `builtins_min` and `builtins_max` explicitly. This fixed the calibration crash and also prevented a second class of bug in the bandpass filter construction (described below).

### Lesson

`from numpy import *` is convenient but dangerous in any code that also uses built-in functions like `max`, `min`, `sum`, or `abs`. Always save the built-ins first if you need them.

---

## 4. Bandpass Filter: ValueError and Order-Independence

### The Problem

The recorded microphone signal contains a lot of unwanted energy — HVAC noise, keyboard clicks, low-frequency rumble from the speaker cabinet, and high-frequency hiss. A bandpass filter was added to the recording pipeline to reject everything outside the chirp band. The filter was designed using `scipy.signal.butter`:

```python
bp_sos = signal.butter(4, [bp_low, bp_high], btype='bandpass', fs=fs, output='sos')
```

This worked fine when `f0 < f1`, but crashed with:

```
ValueError: Wn[0] must be less than Wn[1]
```

whenever the user set `f0 > f1` (sweeping downward in frequency). The `butter` function requires the lower cutoff to come first — it does not sort them automatically.

### The Fix

The filter cutoffs are computed from `f0` and `f1` in an order-independent way:

```python
f_lo = builtins_min(float(f0), float(f1))
f_hi = builtins_max(float(f0), float(f1))
bp_low  = builtins_max(f_lo * 0.85, 100.0)
bp_high = builtins_min(f_hi * 1.15, nyq_cap)
bp_high = builtins_max(bp_high, bp_low + 200.0)
```

This gives a 15% skirt margin on each side of the chirp band, clamps the upper cutoff to 95% of Nyquist to avoid filter instability, and guarantees a minimum bandwidth of 200 Hz so the filter never degenerates.

### Additional Detail

The bandpass filter is applied with **persistent filter state** across chunks:

```python
bp_zi = signal.sosfilt_zi(bp_sos) * 0.0
...
data_flt, bp_zi = signal.sosfilt(bp_sos, data_flt, zi=bp_zi)
```

Without persistent state, each new audio chunk starts with a transient at the filter's initial conditions. This produces a sharp artifact at every chunk boundary — essentially adding 23 artificial spikes per second to the signal. Keeping the filter state continuous eliminates these transients entirely.

---

## 5. The Icicle Pattern: EMA Alpha Tuning

### The Problem

Early waterfall plots showed a distinctive "icicle" or "drip" pattern — bright vertical streaks that persisted for many frames after a target moved away. Instead of showing a clean horizontal line for a stationary target, the display looked like paint dripping downward.

This was caused by the Exponential Moving Average (EMA) used as a Moving Target Indicator (MTI) clutter filter. The EMA removes static background clutter by maintaining a running average of the signal and subtracting it:

```python
background = alpha * background + (1 - alpha) * new_frame
cleaned    = new_frame - background
```

With `alpha = 0.97`, the time constant of the filter is approximately:

```
tau = 1 / (1 - alpha) = 1 / 0.03 ≈ 33 frames
```

This means it took 33 frames (several seconds) for the background model to adapt to a new position. During that time, the previous position remained "hot" in the background estimate, and was subtracted from new frames — causing frames after a target moved to still show residual energy at the old position. Visually, this looked exactly like an icicle dripping from a previous echo.

### The Fix

The alpha was reduced to `0.90`, giving a time constant of 10 frames — roughly 1 second at typical update rates. This is fast enough to adapt quickly when a target moves, while still suppressing truly static clutter (room boundaries, furniture) that doesn't change over dozens of frames.

The trade-off is that faster adaptation means slightly more clutter leakage on completely static scenes, but this was accepted as the right balance for a dynamic sonar where the user is actively moving targets.

---

## 6. Display Gaps and Buffering

### The Problem

The waterfall plot would periodically freeze for half a second, then jump forward several frames at once. This gave the impression of a stuttering or buffering display rather than a smooth scroll.

Two separate causes were identified:

**Cause 1 — Bokeh update rate too high.** The `image_update` thread was calling `source.data = new_data` on every single frame. At 10–12 frames per second, this was flooding the Jupyter/Bokeh rendering pipeline with more updates than it could process, causing a backlog that created the stutter.

**Cause 2 — Per-frame delay re-synchronisation.** The `signal_process` function called `findDelay` (which finds the peak of the cross-correlation) on every frame and subtracted the result from `cur_idx`. In some frames, this subtraction made `cur_idx` go negative or very small, which caused the next several chunks to be processed without emitting any output — creating a visible gap in the waterfall.

### The Fixes

**For the Bokeh rate:** A minimum push interval of 120 ms was introduced, throttling Bokeh updates to approximately 8 fps regardless of how fast data arrived:

```python
now = time.time()
if now - last_push >= 0.12:
    source.data = new_data
    last_push = now
```

**For the re-sync gaps:** The `findDelay` call was moved from every frame to every 60 frames, and a safety bound was added so the correction could never reduce `cur_idx` below zero:

```python
frame_count += 1
if frame_count % 60 == 0:
    idx = findDelay(abs(Xrcv), Nseg)
    safe_correction = cur_idx - Nseg
    if 10 < idx < safe_correction:
        Xrcv = np.roll(Xrcv, -idx)
        Xrcv[-idx:] = 0
        cur_idx -= idx
```

After both fixes, the waterfall scrolled smoothly and continuously without gaps.

---

## 7. Near-Field Dead Zone: The `min_dist_cm` Parameter

### The Problem

In all tests, the very first pixels of the waterfall (the leftmost, shortest-range end) were always saturated bright. This was not from real targets — it was the direct acoustic coupling between the speaker and microphone, which are physically very close on a laptop. The transmitted chirp leaks directly into the microphone, and the matched filter produces a massive peak at near-zero delay, which completely dominated the colour scale and made it impossible to see real targets.

### What Was Tried First

Initially, the waterfall normalisation used the 97th percentile of the entire range line:

```python
scale = np.percentile(new_line_raw, 97)
new_line = new_line_raw / scale
```

This helped but was insufficient — the direct-path peak was so large that even the 97th percentile was influenced by it. All real echoes at longer ranges were compressed into a narrow range of the colour map.

### The Fix: Hard Gate

A `min_dist_cm` parameter was added to `rtsonar()`. Before any normalisation, the first `skip_pixels` pixels (corresponding to the feedthrough zone) are zeroed out:

```python
skip_pixels = int(feedthrough_skip_cm / maxdist * Nplot)
new_line_raw[:skip_pixels] = 0.0
```

The percentile normalisation is then applied only to the gated line, so the colour scale is determined entirely by real echoes at longer ranges. This made an enormous difference — targets at 40–200 cm that were previously invisible became clearly visible.

The `min_dist_cm` parameter is exposed as a configurable value in the notebook, defaulting to 20 cm.

---

## 8. Noise and Clutter Suppression

### The Problem

Even after gating and normalisation, the waterfall showed a lot of background noise — random bright pixels scattered across the entire range. This made it hard to distinguish a real target from clutter.

### Approaches Tried

**3-tap range smoothing** was applied to the matched filter output to suppress pixel-level noise:

```python
kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
Xrcv_seg = np.convolve(Xrcv_seg, kernel, mode='same')
```

This reduced single-pixel spikes without significantly broadening real target peaks.

**CA-CFAR (Cell-Averaging Constant False Alarm Rate)** was implemented as a vectorised function using the cumulative sum trick to avoid Python loops:

```python
def _cfar(x, guard=4, ref=10, factor=4.0):
    cs = np.concatenate([[0.0], np.cumsum(x)])
    # compute local noise estimate from reference cells...
    noise_est = ref_sum / n_ref
    return np.where(x > factor * noise_est, x, 0.0)
```

CFAR works by comparing each range bin to the average power of its surrounding cells (excluding guard cells near the bin of interest). Bins that are not significantly above their local background are zeroed out. This adaptively suppresses range-dependent clutter without needing a fixed threshold.

**Soft noise floor subtraction** was also applied after normalisation:

```python
new_line = np.maximum(new_line - 0.08, 0.0) / 0.92
```

This lifted the effective black level so that low-level noise does not render as dark blue on the jet colourmap (which can still look visually noisy), but instead maps to zero (black).

### Results

The combination of gating, CFAR, range smoothing, and soft floor subtraction produced a significantly cleaner display. Real targets — especially a hand or book moved in front of the laptop — showed as clear, consistent horizontal bands. Background clutter was largely suppressed.

---

## 9. Pulse Compression: The Mismatched Filter

### Background

Standard matched filtering uses the same windowed pulse as both the transmitted signal and the receive template. The Hanning window reduces sidelobes in the compressed pulse (which would otherwise create false echoes on either side of a real target), but it also reduces the transmitted energy.

A technique from radar signal processing called the **mismatched filter** (or pulse compression with window mismatch) separates these two concerns:

- The **transmitted pulse** is unwindowed — this maximises energy on air
- The **receive template** uses a Chebyshev window — this maximises sidelobe suppression

The Chebyshev window with 40 dB attenuation provides approximately 40 dB of sidelobe suppression, compared to ~31 dB for a Hanning window. The trade-off is a small mismatch loss (approximately 1–2 dB of SNR), which is worth it for the significantly cleaner sidelobe structure.

### Implementation

```python
# Transmit: unwindowed real LFM
pulse_tx = np.real(genChirpPulse(Npulse, f0, f1, fs))
ptrain   = genPulseTrain(pulse_tx, Nrep, Nseg)

# Receive template: Chebyshev-windowed complex LFM
cheb_win = signal.windows.chebwin(Npulse, at=40).reshape(Npulse, 1)
pulse_a  = np.multiply(genChirpPulse(Npulse, f0, f1, fs), cheb_win)
```

This approach also decouples range resolution from pulse duration. The range resolution depends only on bandwidth:

```
range_resolution ≈ c / (2 × BW)
```

For a 6–12 kHz sweep (6 kHz bandwidth) at room temperature:

```
range_resolution ≈ 343 / (2 × 6000) ≈ 2.86 cm
```

Increasing Npulse with the same bandwidth does not improve resolution — it only increases the Time-Bandwidth Product (TBP), which improves the SNR of the compressed peak. This was an important insight: **making the pulse longer helps sensitivity but not resolution**.

---

## 10. Parameter Tuning: What Each Parameter Does

Significant time was spent understanding and tuning the five main parameters. Here is what was learned from practical testing:

### `f0` and `f1` — Chirp Frequency Sweep

The bandwidth `BW = |f1 - f0|` is the single most important parameter for range resolution. Wider sweep = finer resolution. The centre frequency affects how much the sound attenuates over distance — higher frequencies attenuate faster in air, so a 15–20 kHz sweep has shorter effective range than a 6–12 kHz sweep.

Practically, sweeps above 18 kHz began showing significantly reduced echo strength at ranges beyond 100 cm on a MacBook Air. The 6–12 kHz band proved the best all-round choice.

### `Npulse` — Pulse Length

Longer pulses transmit more energy and give stronger matched filter returns. However, `Npulse` must be much shorter than `Nseg` — if the pulse is longer than the gap between pulses, consecutive pulses overlap and the cross-correlation becomes ambiguous.

Tested values: 500, 800, 1000, 1200. At 1200 samples (~25 ms at 48 kHz), the returns were noticeably stronger in the presence of background noise. At 500 samples (~10 ms), the display updated slightly faster. 800 was a good compromise.

### `Nseg` — Pulse Repetition Interval

`Nseg` sets how many samples occur between the start of one pulse and the start of the next. It controls two things simultaneously:

- **Max detectable range**: `max_range = c × Nseg / (2 × fs)`
- **Update rate**: one new waterfall line is produced every `Nseg / fs` seconds

This creates a fundamental tension: you want high `Nseg` for long range and low `Nseg` for fast updates.

The audio system reads in chunks of 2048 samples, which sets a hard floor on `Nseg`. Values below 2048 caused buffer overflows in the accumulation logic, breaking the display entirely.

After extensive testing, **`Nseg = 3000`** was found to be nearly optimal across all use cases. At `fs = 48000`:

```
max_range = 343 × 3000 / (2 × 48000) × 100 ≈ 1072 cm
update interval = 3000 / 48000 ≈ 62.5 ms → ~16 lines/sec
```

This gives more than enough range for any indoor scenario while maintaining a responsive update rate. Values like `Nseg = 4096` or `Nseg = 6144` work but are noticeably slower with no meaningful benefit for typical indoor ranges under 5 metres.

### `Nrep` — Pulse Train Repetitions

`Nrep` determines how many pulses are in each transmitted burst. It only affects the vertical height of the waterfall image (how much history is visible). It has no effect on update rate or range. Values of 24–30 gave a comfortable amount of history without making the plot too tall.

### `Nplot` — Horizontal Resolution

`Nplot` is the number of pixels in the range axis of the waterfall. Higher values give finer visual resolution but require more computation per frame for the interpolation step. 200 was found to be a good trade-off — fine enough to distinguish targets a few centimetres apart, fast enough to keep up with the audio processing rate.

---

## 11. Four Tested Parameter Sets

Four distinct configurations were developed and tested, each optimised for a different scenario:

### Set 1 — Balanced (6–12 kHz, Npulse=500, Nseg=4096, maxdist=200 cm)

The starting point for most tests. Detected a hand at ~60 cm clearly. Book reflections appeared as strong horizontal bands. Moving a target back and forth produced a clear diagonal trace on the waterfall. Most reliable set for general use.

### Set 2 — Short Range, High Resolution (6–18 kHz, Npulse=800, Nseg=2048, maxdist=100 cm)

The widest bandwidth tested (12 kHz sweep). Range resolution approximately 1.4 cm — noticeably finer than Set 1. Finger movements at 30–50 cm produced sharp, clean echoes. The narrow maxdist kept the colour scale focused on the near-field region. Limitation: signal attenuated quickly at higher frequencies, so returns beyond ~80 cm were weaker.

### Set 3 — Long Range (6–12 kHz, Npulse=500, Nseg=6144, maxdist=350 cm)

Extended the display to 350 cm to detect targets across a room. Standing up and moving in front of the laptop at ~200 cm produced a visible echo. The larger Nseg made the update rate noticeably slower — this was the set where the `Nseg` optimisation discussion arose. The recommendation of `Nseg=3000` as a better default applies most strongly here.

### Set 4 — High Energy / Noisy Environments (8–16 kHz, Npulse=1200, Nseg=4096, maxdist=200 cm)

Designed for noisier rooms. The longer pulse meant more coherent integration in the matched filter, giving stronger returns. Tested in an environment with significant HVAC noise — targets at 100 cm were still clearly visible where Set 1 showed only noise. The higher centre frequency (8–16 kHz vs 6–12 kHz) shifted the band slightly to avoid some low-frequency noise sources.

---

## 12. The Calibration System

A `calibrate_sonar()` function was developed to automate parameter selection. It runs four phases:

**Phase 1 — Noise floor measurement (4 seconds)**
Records ambient noise, computes RMS and a per-band power spectrum across 2 kHz sub-bands from 3–20 kHz. Identifies the quietest 8 kHz-wide band to inform chirp frequency selection.

**Phase 2 — Config sweep (12 configurations × 2 averaged repetitions)**
Tests a grid of 12 chirp configurations across different frequency bands and pulse lengths. For each, it plays the pulse, records the response, computes matched-filter SNR and dead zone, and averages over 2 repetitions to reduce single-shot noise. Configs are ranked by a score that rewards high mean SNR and penalises high variance.

**Phase 3 — Dead zone verification (3 repetitions)**
Runs the best config 3 times and takes the median dead zone measurement, reducing the effect of single anomalous measurements. The dead zone is detected by finding where the direct-path peak drops below 5% of its maximum — the distance corresponding to this point (plus a 5 cm safety margin) becomes the recommended `min_dist_cm`.

**Phase 4 — Stability analysis (5 repetitions)**
Measures the jitter in peak position (in samples) and the coefficient of variation (CoV) of the peak amplitude. High jitter or high CoV indicates an unstable acoustic environment, and the EMA alpha is set accordingly:
- Stable environment (low jitter, low CoV): alpha = 0.95
- Moderate variability: alpha = 0.90
- High variability: alpha = 0.85 or 0.80

The function returns a dictionary with all recommended parameters, which can be fed directly into the `rtsonar()` call.

---

## 13. Display Rendering: jupyter_bokeh vs push_notebook

### The Problem

Bokeh offers two ways to update a live plot from a background thread in Jupyter:

1. `push_notebook(handle=handle)` — the original method, which works in classic Jupyter but is unreliable in Bokeh 3.x and VS Code's Jupyter extension
2. `BokehModel` from `jupyter_bokeh` — a newer widget-based approach that communicates directly through the Jupyter widget protocol

When using `push_notebook` in VS Code, the plot would either not update at all or update only after a long delay. Switching to `BokehModel(layout)` with `display()` resolved this — updates from background threads appeared in real time.

The code was made adaptive:

```python
if HAVE_JUPYTER_BOKEH:
    display(BokehModel(layout))
    handle = None
else:
    handle = bk.show(layout, notebook_handle=True)
```

The `push_notebook` call is only made when `handle is not None`, so it is automatically skipped when `jupyter_bokeh` is available and handling updates.

---

## 14. Key Findings Summary

| Finding | What Was Wrong | What Fixed It |
|---------|---------------|---------------|
| NumPy shadows `min`/`max` | `from numpy import *` overwrites builtins | Save `builtins_min/max` before import |
| Bandpass ValueError | `f0 > f1` produced invalid filter order | Sort f0/f1 before computing cutoffs |
| Filter transients | Filter state reset every chunk | Persist `bp_zi` across chunks |
| Icicle pattern | EMA alpha=0.97, time constant 33 frames | Reduced to alpha=0.90 (10 frames) |
| Display gaps | Per-frame findDelay caused cur_idx underflow | Re-sync every 60 frames with safety bound |
| Bokeh buffering | Too many source.data updates | Throttle to 120 ms minimum interval |
| Feedthrough dominates display | Direct speaker/mic path saturates near range | Hard gate with min_dist_cm, then normalise |
| Background clutter | Fixed threshold normalisation | CA-CFAR + soft noise floor |
| High sidelobes | Hanning window on both Tx and Rx | Mismatched filter: unwindowed Tx, Chebyshev Rx |
| Slow update with large range | Nseg=6144 much larger than needed | Nseg=3000 covers all practical indoor ranges |

---

## 15. Conclusion

The development of this real-time sonar system involved more engineering in the signal processing and software infrastructure than in the core DSP concepts themselves. The matched filter theory is well-established — the difficulty was making it work reliably in real time on consumer laptop hardware, within a Python threading model, rendered through a Bokeh figure in a Jupyter notebook.

The most impactful individual changes, in rough order of effect:

1. **The `min_dist_cm` dead zone gate** — completely transformed the waterfall from a washed-out, feedthrough-dominated image to a clean, readable display
2. **EMA alpha reduction (0.97 → 0.90)** — eliminated the icicle drip pattern and made the display respond naturally to target movement
3. **Persistent bandpass filter state** — removed chunk-boundary transients that were adding false structure to the signal
4. **CA-CFAR** — adaptively suppressed background clutter without needing manual threshold tuning
5. **Nseg=3000** — the practical sweet spot between update speed and detectable range for indoor environments

The mismatched filter (Chebyshev receive window with unwindowed transmit) provided a meaningful improvement in sidelobe suppression, and the calibration system made the system more accessible to different acoustic environments without manual parameter tuning.

The final system reliably detects hand movements at 30–150 cm, book reflections at 50–200 cm, and whole-body presence at up to 300 cm — all using standard MacBook Air audio hardware with no additional equipment.
