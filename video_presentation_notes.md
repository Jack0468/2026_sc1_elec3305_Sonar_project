# ELEC3305 Sonar Project: Video Demonstration Guide (5–7 Minutes)

This document provides a structured guide and talking points for the video demonstration of the acoustic active sonar system. It is specifically aligned with the implementations, trial-and-error logs, and findings detailed in the project development report.

---

## ⏱️ Video Timeline Overview
```mermaid
gantt
    title Video Presentation Breakdown (5-7 Minutes)
    dateFormat  m:s
    axisFormat %M:%S
    section Intro (User)
    Project Goal & Theory       :active, 0:00, 0:45
    section Code Walkthrough
    Key Code Modules            : 0:45, 2:15
    section Live Demo
    Setup & Real-Time Action    :crit, 2:15, 4:00
    section Engineering
    Challenges & Debugging      : 4:00, 5:15
    section AI Reflection (User)
    LLM Collaboration & Verification : 5:15, 6:30
    section Wrap-Up
    Summary & Q&A               : 6:30, 7:00
```

---

## 1. Project Goal & Operating Principles (0:00 – 0:45)
* **Goal Sentence:** 
  > *"The goal of this project is to implement a real-time, acoustic active sonar system using a laptop's built-in speaker to transmit a chirp pulse and the built-in microphone to record the echoes, applying a matched filter (cross-correlation) to extract time-of-arrival information which is then displayed as a scrolling waterfall plot."*
* **Core Principles:**
  * **LFM Chirps:** Decouples pulse duration ($T$) from signal bandwidth ($B$), allowing long, high-energy pulses to compress into narrow time spikes.
  * **Matched Filtering (Cross-Correlation):** Implemented using `scipy.signal.fftconvolve` to maximize SNR.
  * **Mismatched Filter Design:** Transmit pulse is kept **unwindowed** to maximize the acoustic energy injected into the room. The receive template is windowed using a **Chebyshev window (40 dB attenuation)** to maximize sidelobe suppression, resolving the trade-off between energy detection and range resolution.
  * **Time-of-Flight (ToF):** Converts round-trip delay ($\Delta t$) into distance using the temperature-corrected speed of sound ($v_s \approx 331.3 \sqrt{1 + T_{temp}/273.15}$ m/s), dividing by 2 to account for the reflection path.

---

## 2. Code Structure & Key Modules (0:45 – 2:15)
Show the key files and explain the code architecture:
* **Two main file dependencies:**
  1. [`TimeDomain-RealTime-Sonar.ipynb`](file:///c:/Users/Admin/Documents/Windows_codespace/2026_sc1_elec3305/2026_sc1_elec3305_Sonar_project/TimeDomain-RealTime-Sonar.ipynb): Contains the user configuration, parameter blocks, and DSP helper functions.
  2. [`rtsonar.py`](file:///c:/Users/Admin/Documents/Windows_codespace/2026_sc1_elec3305/2026_sc1_elec3305_Sonar_project/dsp_Sonar_project/rtsonar.py): The core threading engine that coordinates 5 parallel threads:
     * `put_data`: Pushes the pulse train to the playback queue.
     * `play_audio`: Feeds audio to PyAudio speaker streams.
     * `record_audio`: Ingests room audio from the microphone.
     * `signal_process`: Runs bandpass filtering, mismatched filtering, clutter suppression, and normalization.
     * `image_update`: Renders and updates the Bokeh waterfall visualization.
* **Core DSP Package ([`sonar_core/`](file:///c:/Users/Admin/Documents/Windows_codespace/2026_sc1_elec3305/2026_sc1_elec3305_Sonar_project/dsp_Sonar_project/sonar_core/)):**
  * [`dsp.py`](file:///c:/Users/Admin/Documents/Windows_codespace/2026_sc1_elec3305/2026_sc1_elec3305_Sonar_project/dsp_Sonar_project/sonar_core/dsp.py): Clean, pure numpy functions for generating chirps (`genChirpPulse`), matched filtering (`crossCorr`), and delay searching (`findDelay`).
  * [`calibration.py`](file:///c:/Users/Admin/Documents/Windows_codespace/2026_sc1_elec3305/2026_sc1_elec3305_Sonar_project/dsp_Sonar_project/sonar_core/calibration.py): Implementation of the 4-phase automated calibration routine.

---

## 3. Live Demonstration (2:15 – 4:00)
Demonstrate the live system using optimized parameters:
* **The Physical Setup:**
  * Speaker and microphone placed side-by-side on a flat surface facing a target wall/reflector.
  * Disabling OS-level microphone enhancements is highlighted as a critical first step.
* **Tuning Parameters:**
  * **`Nseg = 3000`:** Highlighted as the optimal "sweet spot" for indoor scenarios. At a 48 kHz sampling rate, it offers ~10.7 meters of range with a highly responsive 62.5 ms update interval (~16 lines/sec).
  * **Chirp Sweep (6–12 kHz):** Balanced frequency range to maximize echo strength before high-frequency air attenuation degrades the signal.
* **Waterfall Plot Visuals:**
  * Point out the **direct feedthrough gate** (using `min_dist_cm` to zero out the first 20 cm) which prevents the speaker-microphone direct coupling from saturating the color scale.
  * Move a hand or book at ranges between 40 cm to 2 meters and point out the clean, moving diagonal lines tracing target movements.
  * Point out the smooth scrolling achieved by switching to `jupyter_bokeh` (`BokehModel`) which bypassed the unreliable thread updates of `push_notebook` in VS Code.

---

## 4. Technical Challenges & Debugging (4:00 – 5:15)
Highlight the major engineering hurdles and the solutions implemented:
* **The "Icicle" Decay Pattern:**
  * *Problem:* Stationary targets left trailing vertical "drips" after moving away.
  * *Fix:* Discovered that the Exponential Moving Average (EMA) clutter filter had an alpha of `0.97` (creating a sluggish 33-frame decay time). Tuned alpha down to `0.90` (10-frame decay time) to allow rapid adaptation to target movement.
* **Display Stuttering & Gaps:**
  * *Problem:* Scrolling periodically froze, then jumped forward.
  * *Fixes:* Throttled Bokeh queue updates to a minimum interval of 120 ms (8 fps) to avoid clogging the rendering pipeline. Moved the `findDelay` re-synchronization call from every frame to once every 60 frames to prevent `cur_idx` underflows.
* **Filter Boundary Transients:**
  * *Problem:* Audio chunk divisions introduced sharp artifacts (23 spikes per second) into the matched filter.
  * *Fix:* Used persistent state filtering (`signal.sosfilt` with `zi` tracking) to maintain continuous boundary conditions across audio chunks.

---

## 5. AI Assistance, Limitations & Verification (5:15 – 6:30)
Reflect on collaborating with AI (such as LLMs) during development:
* **How LLMs Were Used:**
  * Scaffolding the PyAudio callback threads and Bokeh interface logic.
  * Implementing DSP mathematical models and providing initial blueprints for clutter filters (CA-CFAR and EMA).
* **Limitations and Silent Bugs Introduced:**
  * **NumPy Namespace Collision:** The LLM suggested wildcard imports (`from numpy import *`), which silently shadowed Python's built-in `max` and `min` functions. This caused a catastrophic crash in `calibrate_sonar` (`AxisError: axis 12288 is out of bounds...`) when comparing integers.
  * **Unsorted Filter Boundaries:** AI-generated bandpass code crashed (`ValueError: Wn[0] must be less than Wn[1]`) when attempting downward chirp sweeps because it didn't sort the frequency cutoffs (`f0` and `f1`) beforehand.
  * **Transient Neglect:** Initial AI scripts applied standard filters to isolated audio chunks, ignoring the state variables (`zi`) needed to prevent chunk-boundary transients.
* **Verification and Improvement Strategy:**
  * **Automated Calibration Script:** Built a 4-phase testing pipeline (`calibrate_sonar`) to measure noise floors, run sweep test matrices (averaging repetitions to filter anomalous noise), detect feedthrough boundaries, and analyze peak jitter to adaptively set EMA weights.
  * **Modular DSP Isolation:** Disconnected the UI and audio hardware dependencies into pure-NumPy modules under `sonar_core/` to run unit tests and ensure math calculations were 100% correct.
