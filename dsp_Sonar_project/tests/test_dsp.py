"""
Unit tests for sonar_core.dsp

Run with::

    python -m pytest tests/test_dsp.py -v
"""

import numpy as np
import pytest

from sonar_core.dsp import (
    genChirpPulse,
    genPulseTrain,
    crossCorr,
    findDelay,
    dist2time,
    time2dist,
)


# ── genChirpPulse ─────────────────────────────────────────────────────────

class TestGenChirpPulse:
    def test_output_shape(self, default_fs):
        Npulse = 512
        pulse = genChirpPulse(Npulse, 6000, 12000, default_fs)
        assert pulse.shape == (Npulse, 1)

    def test_output_is_complex(self, default_fs):
        pulse = genChirpPulse(256, 6000, 12000, default_fs)
        assert np.iscomplexobj(pulse)

    def test_unit_magnitude(self, default_fs):
        """The analytic chirp should have magnitude ≈ 1 everywhere."""
        pulse = genChirpPulse(512, 6000, 12000, default_fs)
        magnitudes = np.abs(pulse.ravel())
        np.testing.assert_allclose(magnitudes, 1.0, atol=1e-10)


# ── genPulseTrain ─────────────────────────────────────────────────────────

class TestGenPulseTrain:
    def test_output_length(self, default_fs):
        Npulse, Nseg, Nrep = 500, 4096, 24
        pulse = genChirpPulse(Npulse, 6000, 12000, default_fs)
        train = genPulseTrain(pulse, Nrep, Nseg)
        assert len(train) == Nseg * Nrep

    def test_output_is_real(self, default_fs):
        pulse = genChirpPulse(256, 6000, 12000, default_fs)
        train = genPulseTrain(pulse, 10, 2048)
        assert not np.iscomplexobj(train)

    def test_pulse_present_in_each_segment(self, default_fs):
        Npulse, Nseg, Nrep = 256, 2048, 4
        pulse = genChirpPulse(Npulse, 6000, 12000, default_fs)
        train = genPulseTrain(pulse, Nrep, Nseg)
        for i in range(Nrep):
            seg = train[i * Nseg: (i + 1) * Nseg]
            # Pulse region should be non‑zero
            assert np.any(seg[:Npulse] != 0)
            # Padding should be zero
            np.testing.assert_array_equal(seg[Npulse:], 0)


# ── crossCorr ─────────────────────────────────────────────────────────────

class TestCrossCorr:
    def test_output_length(self, default_fs):
        Npulse = 256
        chunk_len = 2048
        pulse = genChirpPulse(Npulse, 6000, 12000, default_fs)
        chunk = np.random.randn(chunk_len)
        result = crossCorr(chunk, pulse)
        expected_len = chunk_len + Npulse - 1
        assert len(result) == expected_len

    def test_peak_at_correct_delay(self, default_fs):
        """Inject a copy of the pulse at a known offset and verify the peak."""
        Npulse = 256
        pulse = genChirpPulse(Npulse, 6000, 12000, default_fs)
        pulse_real = np.real(pulse.ravel())

        # Create a signal with the pulse at sample 1000
        delay = 1000
        sig = np.zeros(4096)
        sig[delay: delay + Npulse] = pulse_real

        cc = crossCorr(sig, pulse)
        peak_idx = np.argmax(np.abs(cc))
        # Peak should be at (delay + Npulse - 1) due to convolution indexing
        assert abs(peak_idx - (delay + Npulse - 1)) <= 2


# ── findDelay ─────────────────────────────────────────────────────────────

class TestFindDelay:
    def test_returns_int(self):
        arr = np.array([0, 1, 3, 2, 0])
        assert isinstance(findDelay(arr, len(arr)), int)

    def test_finds_correct_peak(self):
        arr = np.zeros(100)
        arr[42] = 10
        assert findDelay(arr, 100) == 42


# ── dist2time / time2dist ─────────────────────────────────────────────────

class TestDistTimeConversions:
    def test_roundtrip(self):
        """dist → time → dist should be identity."""
        for dist in [10, 50, 100, 200]:
            t = dist2time(dist, temperature=21)
            recovered = time2dist(t, temperature=21)
            np.testing.assert_allclose(recovered, dist, atol=1e-8)

    def test_speed_of_sound_at_0C(self):
        """At 0 °C the speed of sound should be 331.3 m/s."""
        t = dist2time(100, temperature=0)
        # Round‑trip for 1 m = 2 / 331.3 s
        expected = 2.0 / 331.3
        np.testing.assert_allclose(t, expected, rtol=1e-6)

    def test_positive(self):
        assert dist2time(100) > 0
        assert time2dist(0.01) > 0
