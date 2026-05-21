"""
sonar_core.engine
=================

GUI‑agnostic real‑time sonar engine.

The engine manages three background threads (play, record, process) and
pushes processed sonar frames into a thread‑safe ``queue.Queue`` that
any front‑end (PyQtGraph, Flask/WebSocket, etc.) can consume.
"""

import threading
import time
import queue
import sys

import numpy as np
import pyaudio
try:
    import sounddevice as sd
except (ImportError, OSError):
    sd = None
from scipy import interpolate
from scipy import signal as sp_signal

from sonar_core.dsp import (
    genChirpPulse,
    genPulseTrain,
    crossCorr,
    findDelay,
    dist2time,
)

def ca_cfar_1d(x: np.ndarray, guard: int = 3, ref: int = 8, factor: float = 2.5) -> np.ndarray:
    """
    1D Cell-Averaging Constant False Alarm Rate (CA-CFAR) filter.
    Vectorised implementation using convolution.
    """
    kernel = np.zeros(2 * (ref + guard) + 1, dtype=np.float32)
    kernel[:ref] = 1.0
    kernel[-ref:] = 1.0
    
    ref_sum = np.convolve(x, kernel, mode='same')
    ref_count = np.convolve(np.ones_like(x, dtype=np.float32), kernel, mode='same')
    
    noise_est = ref_sum / np.maximum(ref_count, 1.0)
    return np.where(x > factor * noise_est, x, 0.0)



# ---------------------------------------------------------------------------
# Internal thread targets
# ---------------------------------------------------------------------------

def _put_data(Qout: queue.Queue, ptrain: np.ndarray,
              Twait: float, stop_flag: threading.Event) -> None:
    """Continuously feed the pulse train into the output queue."""
    while not stop_flag.is_set():
        if Qout.qsize() < 2:
            Qout.put(ptrain)
        time.sleep(Twait)
    Qout.put("EOT")


def _play_audio(Qout: queue.Queue, fs: float,
                stop_flag: threading.Event,
                dev=None) -> None:
    """
    Play audio from the output queue through the speaker using sounddevice.

    sounddevice accepts numpy arrays directly and avoids the PyAudio
    PY_SSIZE_T_CLEAN incompatibility with Python 3.10+.
    Writes in 4096-sample chunks so the thread stays responsive.
    """
    CHUNK = 4096

    try:
        stream = sd.OutputStream(
            samplerate=int(fs),
            channels=1,
            dtype='float32',
            device=dev,
            blocksize=CHUNK,
        )
        stream.start()
        print(f"[audio] Output stream opened (sounddevice, float32, "
              f"{int(fs)} Hz, device={dev})")
    except Exception as e:
        print(f"[audio] FAILED to open output stream: {e}")
        return

    pos = 0
    data = None

    while not stop_flag.is_set():
        # Fetch a new ptrain when the current one is exhausted
        if data is None or pos >= len(data):
            try:
                item = Qout.get(timeout=0.5)
            except Exception:
                continue
            if str(item) == "EOT":
                break
            # Normalise to full scale so the chirp is clearly audible
            peak = float(np.max(np.abs(item)))
            data = (item / max(peak, 1e-6)).astype(np.float32)
            pos = 0

        chunk = data[pos: pos + CHUNK]
        pos += CHUNK

        try:
            # sounddevice expects shape (frames, channels)
            stream.write(chunk.reshape(-1, 1))
        except Exception as e:
            print(f"[audio] Write error: {e}")
            break

    try:
        stream.stop()
        stream.close()
    except Exception:
        pass
    print("[audio] Output stream closed.")


def _record_audio(Qin: queue.Queue, p: pyaudio.PyAudio,
                  fs: float, stop_flag: threading.Event,
                  dev=None, chunk: int = 2048,
                  mic_level_callback=None) -> None:
    """Record audio from the microphone into the input queue."""
    istream = p.open(
        format=pyaudio.paFloat32,
        channels=1,
        rate=int(fs),
        input=True,
        input_device_index=dev,
        frames_per_buffer=chunk,
    )
    while not stop_flag.is_set():
        try:
            data_str = istream.read(chunk, exception_on_overflow=False)
        except Exception:
            print("Unexpected error:", sys.exc_info()[0])
            break

        data_flt = np.frombuffer(data_str, "float32")

        # Optional mic‑level callback (for GUI indicators)
        if mic_level_callback is not None:
            mic_level_callback(float(np.max(np.abs(data_flt))))

        Qin.put(data_flt)

    istream.stop_stream()
    istream.close()
    Qin.put("EOT")


def _signal_process(Qin: queue.Queue, Qdata: queue.Queue,
                    pulse_a: np.ndarray, Nseg: int, Nplot: int,
                    fs: float, maxdist: float, temperature: float,
                    bp_sos: np.ndarray,
                    stop_flag: threading.Event) -> None:
    """
    Overlap‑and‑add matched filtering with bandpass, clutter and noise suppression.

    Produces interpolated sonar frames of length *Nplot* and pushes
    them into *Qdata*.
    """
    buf_size  = 3 * Nseg
    pulse_len = len(np.ravel(pulse_a))

    def _fresh_buf():
        return np.zeros(buf_size, dtype="complex")

    Xrcv = _fresh_buf()
    cur_idx = 0
    found_delay = False

    maxsamp = min(int(dist2time(maxdist, temperature) * fs), Nseg)

    # Initialize persistent state variables for filtering
    bp_zi = sp_signal.sosfilt_zi(bp_sos) * 0.0
    background = None
    alpha = 0.90
    frame_count = 0

    while not stop_flag.is_set():
        chunk = Qin.get()
        if isinstance(chunk, str) and chunk == "EOT":
            break

        # Apply bandpass filter with persistent state to avoid chunk boundary transients
        chunk_filtered, bp_zi = sp_signal.sosfilt(bp_sos, chunk, zi=bp_zi)
        Xchunk = crossCorr(chunk_filtered, pulse_a)   # length: len(chunk) + pulse_len - 1
        corr_len = len(chunk) + pulse_len - 1

        # Safety: if the buffer shape was corrupted by np.roll edge-cases,
        # reinitialise with a fresh allocation (never slice-assign into a
        # potentially zero-length array).
        if len(Xrcv) != buf_size or cur_idx < 0 or cur_idx >= buf_size:
            Xrcv = _fresh_buf()
            cur_idx = 0
            found_delay = False

        # Overlap-and-add
        end_idx = cur_idx + corr_len
        if end_idx <= buf_size:
            Xrcv[cur_idx:end_idx] += Xchunk[:corr_len]
        else:
            available = buf_size - cur_idx
            if available > 0:
                Xrcv[cur_idx:] += Xchunk[:available]

        cur_idx += len(chunk)

        if found_delay and cur_idx >= Nseg:
            frame_count += 1
            # Adjust delay/sync alignment every 60 frames instead of every frame to avoid jitter and gaps
            if frame_count % 60 == 0:
                idx = findDelay(abs(Xrcv), Nseg)
                safe_correction = cur_idx - Nseg
                if 10 < idx < safe_correction:
                    Xrcv = np.roll(Xrcv, -idx)
                    if 0 < idx < buf_size:
                        Xrcv[-idx:] = 0
                    cur_idx = max(cur_idx - idx, 0)

            # Crop, normalise, interpolate to Nplot output pixels
            seg = abs(Xrcv[:maxsamp].copy())
            peak_val = float(abs(Xrcv[0]))
            Xrcv_seg = (seg / max(peak_val, 1e-5)) ** 0.5

            x_old = np.arange(maxsamp)
            interp_fn = interpolate.interp1d(x_old, Xrcv_seg)
            x_new = np.linspace(0, maxsamp - 1, Nplot)
            Xrcv_seg = interp_fn(x_new)

            # 1. CA-CFAR Clutter Suppression
            Xrcv_seg = ca_cfar_1d(Xrcv_seg, guard=3, ref=8, factor=2.5)

            # 2. Moving Target Indicator (MTI) clutter suppression via EMA
            if background is None:
                background = Xrcv_seg.copy()
            else:
                background = alpha * background + (1 - alpha) * Xrcv_seg
            Xrcv_seg = np.maximum(Xrcv_seg - background, 0.0)

            # 3. 3-tap range smoothing
            smooth_kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
            Xrcv_seg = np.convolve(Xrcv_seg, smooth_kernel, mode='same')

            # 4. Soft noise floor subtraction
            Xrcv_seg = np.maximum(Xrcv_seg - 0.08, 0.0) / 0.92

            # Remove the processed segment
            Xrcv = np.roll(Xrcv, -Nseg)
            # buf_size is always >= Nseg (buf_size = 3*Nseg), so this is safe
            Xrcv[-Nseg:] = 0
            cur_idx = max(cur_idx - Nseg, 0)

            Qdata.put(Xrcv_seg)

        elif cur_idx > 2 * Nseg:
            # Initial synchronisation: align to pulse boundary
            idx = findDelay(abs(Xrcv), Nseg)
            Xrcv = np.roll(Xrcv, -idx)
            if 0 < idx < buf_size:
                Xrcv[-idx:] = 0
            cur_idx = max(cur_idx - idx - 1, 0)
            found_delay = True

    Qdata.put("EOT")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SonarEngine:
    """
    GUI‑agnostic sonar engine.

    Start the engine, then call :meth:`get_frame` in a loop to receive
    processed sonar lines (1‑D ``numpy`` arrays of length *Nplot*).

    Parameters
    ----------
    f0 : float
        Chirp start frequency (Hz).
    f1 : float
        Chirp end frequency (Hz).
    fs : float
        Sampling frequency (Hz).
    Npulse : int
        Chirp pulse length in samples.
    Nseg : int
        Segment length (samples between pulses).
    Nrep : int
        Number of repetitions in the pulse train.
    Nplot : int
        Number of output pixels per sonar line.
    maxdist : float
        Maximum detection distance (cm).
    temperature : float
        Ambient temperature (°C).
    input_device_index : int or None
        PyAudio input device index (``None`` for default).
    output_device_index : int or None
        PyAudio output device index (``None`` for default).
    mic_level_callback : callable or None
        Optional callback ``f(level: float)`` invoked with the current
        peak mic level each time a chunk is recorded.
    """

    def __init__(
        self,
        f0: float,
        f1: float,
        fs: float,
        Npulse: int,
        Nseg: int,
        Nrep: int,
        Nplot: int,
        maxdist: float,
        temperature: float,
        input_device_index=None,
        output_device_index=None,
        mic_level_callback=None,
    ):
        self.f0 = f0
        self.f1 = f1
        self.fs = fs
        self.Npulse = int(Npulse)
        self.Nseg = int(Nseg)
        self.Nrep = int(Nrep)
        self.Nplot = int(Nplot)
        self.maxdist = maxdist
        self.temperature = temperature
        self.input_device_index = input_device_index
        self.output_device_index = output_device_index
        self.mic_level_callback = mic_level_callback

        # Build the chirp pulse and pulse train using mismatched filter design
        # Transmit: Unwindowed real LFM chirp (maximum energy on air)
        pulse_tx = np.real(genChirpPulse(self.Npulse, self.f0, self.f1, self.fs))
        self._ptrain = genPulseTrain(pulse_tx, self.Nrep, self.Nseg)

        # Receive: Chebyshev-windowed LFM template (max sidelobe suppression, e.g. 40 dB)
        cheb_win = sp_signal.windows.chebwin(self.Npulse, at=40).reshape(self.Npulse, 1)
        self._pulse_a = np.multiply(genChirpPulse(self.Npulse, self.f0, self.f1, self.fs), cheb_win)

        # Precompute bandpass filter coefficients (order-independent)
        f_lo = min(self.f0, self.f1)
        f_hi = max(self.f0, self.f1)
        bp_low = max(f_lo * 0.85, 100.0)
        nyq_cap = self.fs / 2 * 0.95
        bp_high = min(f_hi * 1.15, nyq_cap)
        bp_high = max(bp_high, bp_low + 200.0)
        self._bp_sos = sp_signal.butter(4, [bp_low, bp_high], btype='bandpass', fs=self.fs, output='sos')

        # Internal queues
        self._Qin: queue.Queue = queue.Queue()
        self._Qout: queue.Queue = queue.Queue()
        self._Qdata: queue.Queue = queue.Queue()

        # Threading
        self._stop_flag = threading.Event()
        self._threads: list[threading.Thread] = []
        self._pyaudio: pyaudio.PyAudio | None = None
        self._started = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start audio I/O and signal‑processing threads."""
        self._stop_flag.clear()
        self._started = True
        self._pyaudio = pyaudio.PyAudio()

        self._threads = [
            threading.Thread(
                target=_put_data,
                args=(self._Qout, self._ptrain,
                      self.Nseg / self.fs * 3, self._stop_flag),
            ),
            threading.Thread(
                target=_record_audio,
                args=(self._Qin, self._pyaudio, self.fs, self._stop_flag,
                      self.input_device_index, 2048, self.mic_level_callback),
            ),
            threading.Thread(
                target=_play_audio,
                args=(self._Qout, self.fs, self._stop_flag,
                      self.output_device_index),
            ),
            threading.Thread(
                target=_signal_process,
                args=(self._Qin, self._Qdata, self._pulse_a,
                      self.Nseg, self.Nplot, self.fs,
                      self.maxdist, self.temperature, self._bp_sos, self._stop_flag),
            ),
        ]
        for t in self._threads:
            t.daemon = True
            t.start()

    def stop(self) -> None:
        """Signal all threads to stop and wait for them to finish."""
        self._stop_flag.set()
        for t in self._threads:
            t.join(timeout=5)
        if self._pyaudio is not None:
            self._pyaudio.terminate()
            self._pyaudio = None

    @property
    def running(self) -> bool:
        return self._started and not self._stop_flag.is_set()

    @property
    def stop_flag(self) -> threading.Event:
        return self._stop_flag

    # -- data access ---------------------------------------------------------

    def get_frame(self, timeout: float = 1.0):
        """
        Block until a processed sonar line is available.

        Returns
        -------
        np.ndarray or None
            1‑D array of length *Nplot*, or ``None`` if no data arrived
            within *timeout* seconds or the engine has stopped.
        """
        try:
            data = self._Qdata.get(timeout=timeout)
            if isinstance(data, str) and data == "EOT":
                return None
            return data
        except queue.Empty:
            return None
