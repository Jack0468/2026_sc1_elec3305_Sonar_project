"""
sonar_core.dsp
==============

Core DSP functions for the sonar system. These are the validated
implementations derived from the Sonar Lab Walkthrough (01_Sonar_Lab_Walkthrough.ipynb).

All functions are pure‑NumPy / SciPy and have no GUI dependencies.
"""

import numpy as np
from scipy import signal


def genChirpPulse(Npulse: int, f0: float, f1: float, fs: float) -> np.ndarray:
    """
    Generate a complex analytic chirp pulse.

    Parameters
    ----------
    Npulse : int
        Number of samples in the pulse.
    f0 : float
        Start frequency of the chirp (Hz).
    f1 : float
        End frequency of the chirp (Hz).
    fs : float
        Sampling frequency (Hz).

    Returns
    -------
    np.ndarray
        Complex analytic chirp pulse of shape ``(Npulse, 1)``.
    """
    t = np.arange(int(Npulse)) / fs
    T = Npulse / fs
    pulse = np.exp(1j * 2 * np.pi * (f0 * t + ((f1 - f0) / (2 * T)) * (t ** 2)))
    return pulse.reshape(-1, 1)


def genPulseTrain(pulse: np.ndarray, Nrep: int, Nseg: int) -> np.ndarray:
    """
    Generate a pulse train by repeating a zero‑padded pulse.

    Parameters
    ----------
    pulse : np.ndarray
        The chirp pulse (1‑D or 2‑D column vector).
    Nrep : int
        Number of repetitions.
    Nseg : int
        Length of each segment (pulse + zero‑padding).

    Returns
    -------
    np.ndarray
        1‑D real‑valued pulse train of length ``Nrep * Nseg``.
    """
    pulse = np.ravel(pulse)
    pulse_padded = np.zeros(int(Nseg))
    pulse_padded[: len(pulse)] = np.real(pulse)
    return np.tile(pulse_padded, int(Nrep))


def crossCorr(rcv: np.ndarray, pulse_a: np.ndarray) -> np.ndarray:
    """
    Matched‑filter cross‑correlation via FFT convolution.

    Parameters
    ----------
    rcv : np.ndarray
        Received signal chunk.
    pulse_a : np.ndarray
        Analytic (complex) reference pulse.

    Returns
    -------
    np.ndarray
        Cross‑correlation result (1‑D).
    """
    rcv = np.ravel(rcv)
    pulse_a = np.ravel(pulse_a)
    return signal.fftconvolve(rcv, np.conj(pulse_a)[::-1], mode="full")


def findDelay(Xrcv: np.ndarray, Nseg: int) -> int:
    """
    Find the sample index of the peak in the cross‑correlation output.

    Parameters
    ----------
    Xrcv : np.ndarray
        Cross‑correlation magnitude array.
    Nseg : int
        Segment length — search window is limited to ``[0, Nseg)``.

    Returns
    -------
    int
        Index of the peak.
    """
    return int(np.argmax(np.abs(np.ravel(Xrcv)[: int(Nseg)])))


def dist2time(dist: float, temperature: float = 21.0) -> float:
    """
    Convert one‑way distance (cm) to round‑trip propagation time (s).

    Parameters
    ----------
    dist : float
        Distance in **centimetres**.
    temperature : float
        Ambient temperature in °C (default 21).

    Returns
    -------
    float
        Round‑trip time in seconds.
    """
    speed_of_sound = 331.3 * np.sqrt(1 + temperature / 273.15)
    return float(2 * (float(dist) / 100.0) / speed_of_sound)


def time2dist(t: float, temperature: float = 21.0) -> float:
    """
    Convert round‑trip propagation time (s) to one‑way distance (cm).

    Parameters
    ----------
    t : float
        Round‑trip time in seconds.
    temperature : float
        Ambient temperature in °C (default 21).

    Returns
    -------
    float
        Distance in centimetres.
    """
    speed_of_sound = 331.3 * np.sqrt(1 + temperature / 273.15)
    return float((t * speed_of_sound / 2) * 100)
