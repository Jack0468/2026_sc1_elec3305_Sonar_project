"""
sonar_core.calibration
======================

Automated calibration script that characterises the speaker/microphone
system and recommends optimal sonar parameters for the current hardware
and environment.

Usage (CLI)::

    python -m sonar_core.calibration

The script will:

1. Enumerate audio devices and let the user pick one.
2. Play a wideband chirp and record the response to measure the
   speaker‑mic frequency response.
3. Identify the frequency band with the best SNR.
4. Test several pulse‑length / bandwidth combinations and rank them
   by matched‑filter SNR.
5. Write the recommended parameters to ``calibration_result.json``.
"""

import json
import os
import sys
import time
import threading
import queue

import numpy as np
import pyaudio
from scipy import signal as sp_signal

from sonar_core.dsp import genChirpPulse, genPulseTrain, crossCorr


# ── helpers ──────────────────────────────────────────────────────────────

def _list_input_devices(p: pyaudio.PyAudio):
    """Return a list of (index, name) for input‑capable devices."""
    devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            devices.append((i, info["name"]))
    return devices


def _play_and_record(sig: np.ndarray, fs: float,
                     input_dev=None, output_dev=None,
                     extra_secs: float = 1.0) -> np.ndarray:
    """
    Simultaneously play *sig* and record the microphone response.

    Uses ``sounddevice`` for output (avoids the PyAudio PY_SSIZE_T_CLEAN
    incompatibility with Python 3.10+) and PyAudio for input recording.

    Returns the recorded 1‑D float32 array.
    """
    import sounddevice as sd

    p = pyaudio.PyAudio()
    Qin: queue.Queue = queue.Queue()

    n_record = int(len(sig) + extra_secs * fs)
    playback = np.zeros(n_record, dtype=np.float32)
    playback[: len(sig)] = sig.astype(np.float32)

    # ── input (PyAudio) ──
    chunk = 2048
    istream = p.open(format=pyaudio.paFloat32, channels=1,
                     rate=int(fs), input=True,
                     input_device_index=input_dev,
                     frames_per_buffer=chunk)

    stop = threading.Event()

    def _rec():
        while not stop.is_set():
            try:
                raw = istream.read(chunk, exception_on_overflow=False)
                Qin.put(np.frombuffer(raw, dtype="float32"))
            except Exception:
                break

    rec_thread = threading.Thread(target=_rec, daemon=True)
    rec_thread.start()

    # ── output (sounddevice — no PY_SSIZE_T_CLEAN issue) ──
    try:
        sd.play(playback.reshape(-1, 1), samplerate=int(fs), device=output_dev)
        sd.wait()
    except Exception as e:
        print(f"[calibration] Audio playback error: {e}")

    time.sleep(0.3)
    stop.set()
    rec_thread.join(timeout=2)

    istream.stop_stream()
    istream.close()
    p.terminate()

    chunks = []
    while not Qin.empty():
        chunks.append(Qin.get())
    if chunks:
        return np.concatenate(chunks)
    return np.array([], dtype=np.float32)



def _measure_frequency_response(fs: float, input_dev=None, output_dev=None):
    """
    Play a wideband chirp (1 kHz → 22 kHz) and return the magnitude
    frequency response of the speaker→microphone path.

    Returns (freqs, magnitude).
    """
    T = 2.0  # seconds
    f0, f1 = 1000, 22000
    t = np.arange(0, T, 1 / fs)
    chirp = 0.5 * np.sin(2 * np.pi * (f0 * t + ((f1 - f0) / (2 * T)) * t ** 2))

    rcv = _play_and_record(chirp.astype(np.float32), fs,
                           input_dev=input_dev, output_dev=output_dev,
                           extra_secs=1.0)

    if len(rcv) == 0:
        return np.array([]), np.array([])

    freqs, mag = sp_signal.freqz(rcv, 1, worN=4096, fs=fs)
    return freqs, np.abs(mag)


def _score_params(fs, f0, f1, Npulse, rcv_response, freqs_resp):
    """
    Estimate a rough SNR score for a given parameter set based on the
    measured frequency response.  Higher is better.
    """
    # Energy of the response in the [f0, f1] band
    mask = (freqs_resp >= f0) & (freqs_resp <= f1)
    if not np.any(mask):
        return 0.0
    band_energy = np.mean(rcv_response[mask] ** 2)

    # Matched filter gain scales roughly as sqrt(Npulse * BW)
    bw = f1 - f0
    mf_gain = np.sqrt(Npulse * bw / fs)

    return float(band_energy * mf_gain)


# ── main calibration routine ─────────────────────────────────────────────

def calibrate(input_dev=None, output_dev=None, fs: float = 48000,
              output_path: str = "calibration_result.json",
              interactive: bool = True) -> dict:
    """
    Run the full calibration procedure.

    Parameters
    ----------
    input_dev : int or None
        Microphone device index.
    output_dev : int or None
        Speaker device index.
    fs : float
        Sampling rate.
    output_path : str
        Where to write the JSON results.
    interactive : bool
        If ``True``, print progress to stdout.

    Returns
    -------
    dict
        Recommended parameter dictionary.
    """
    def _log(msg):
        if interactive:
            print(msg)

    _log("=" * 60)
    _log("  SONAR CALIBRATION")
    _log("=" * 60)

    # Step 1 ─ measure frequency response
    _log("\n[Step 1/4] Measuring speaker‑mic frequency response …")
    _log("  (a chirp will play — keep the environment quiet)")
    time.sleep(1)
    freqs, mag = _measure_frequency_response(fs, input_dev, output_dev)

    if len(freqs) == 0:
        _log("  ERROR: no audio was recorded. Check device indices.")
        return {}

    _log(f"  Measured {len(freqs)} frequency bins.")

    # Step 2 ─ find best frequency band
    _log("\n[Step 2/4] Identifying best frequency band …")

    candidates = [
        (4000, 8000),
        (6000, 12000),
        (8000, 16000),
        (10000, 18000),
        (12000, 20000),
        (15000, 19000),
        (16000, 20000),
        (18000, 22000),
    ]

    best_band = None
    best_score = -1

    for f0_c, f1_c in candidates:
        for Np in [256, 512, 1024]:
            score = _score_params(fs, f0_c, f1_c, Np, mag, freqs)
            if score > best_score:
                best_score = score
                best_band = (f0_c, f1_c, Np)

    f0_opt, f1_opt, Npulse_opt = best_band
    _log(f"  Best band: {f0_opt}–{f1_opt} Hz, Npulse={Npulse_opt}  (score={best_score:.4f})")

    # Step 3 ─ recommend remaining params
    _log("\n[Step 3/4] Computing remaining parameters …")
    temperature = 21.0  # default — user can override
    Nseg = 4096
    Nrep = 24
    Nplot = 200
    maxdist = 200  # cm

    result = {
        "fs": fs,
        "f0": f0_opt,
        "f1": f1_opt,
        "Npulse": Npulse_opt,
        "Nseg": Nseg,
        "Nrep": Nrep,
        "Nplot": Nplot,
        "maxdist": maxdist,
        "temperature": temperature,
        "input_device_index": input_dev,
        "calibration_score": best_score,
    }

    # Step 4 ─ write to file
    _log(f"\n[Step 4/4] Writing results to {output_path}")
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)

    _log("\n  Recommended parameters:")
    for k, v in result.items():
        _log(f"    {k}: {v}")
    _log("\nCalibration complete.")
    return result


# ── CLI entry‑point ──────────────────────────────────────────────────────

def main():
    p = pyaudio.PyAudio()
    devices = _list_input_devices(p)
    p.terminate()

    print("\nAvailable input devices:")
    for idx, name in devices:
        print(f"  [{idx}] {name}")

    inp = input("\nEnter input device index (or press Enter for default): ").strip()
    input_dev = int(inp) if inp else None

    calibrate(input_dev=input_dev)


if __name__ == "__main__":
    main()
