"""
flask_app.sonar_bridge
======================

Bridge between :class:`sonar_core.engine.SonarEngine` and the
Flask‑SocketIO transport layer.

This class manages engine lifecycle and converts numpy arrays into
JSON‑serialisable lists for WebSocket emission.

Fix notes
---------
* The pump now uses ``socketio.start_background_task`` instead of a plain
  ``threading.Thread``.  This is required so that Flask‑SocketIO can
  correctly dispatch the ``emit`` call through its own event loop,
  ensuring continuous delivery of ``sonar_data`` events to the browser.
* ``socketio.sleep(0)`` yields control inside the loop so that the
  SocketIO server can process other events (connect/disconnect, etc.)
  without being starved by the tight frame loop.
"""

import sys
import os

import numpy as np

# Ensure the parent directory (dsp_Sonar_project) is on sys.path so that
# ``sonar_core`` can be imported regardless of where Flask is launched from.
_parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

from sonar_core.engine import SonarEngine  # noqa: E402


class SonarBridge:
    """
    Wraps :class:`SonarEngine` and provides a continuous streaming
    interface suitable for Flask‑SocketIO.

    Parameters
    ----------
    socketio
        The Flask‑SocketIO instance used to emit events.
    """

    def __init__(self, socketio):
        self.socketio = socketio
        self.engine: SonarEngine | None = None
        self._mic_level: float = 0.0
        self._frame_count: int = 0
        self._running: bool = False
        self._smoothed_dist: float = 0.0   # EMA for jitter reduction

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self, params: dict) -> None:
        """Start a new sonar engine with the given *params* dict."""
        self.stop()  # tear down any previous engine first

        def _mic_cb(level: float):
            self._mic_level = level

        self.engine = SonarEngine(
            f0=float(params.get("f0", 6000)),
            f1=float(params.get("f1", 12000)),
            fs=float(params.get("fs", 48000)),
            Npulse=int(params.get("Npulse", 500)),
            Nseg=int(params.get("Nseg", 4096)),
            Nrep=int(params.get("Nrep", 24)),
            Nplot=int(params.get("Nplot", 200)),
            maxdist=float(params.get("maxdist", 200)),
            temperature=float(params.get("temperature", 21)),
            input_device_index=params.get("input_device_index"),
            mic_level_callback=_mic_cb,
        )
        self.engine.start()
        self._frame_count = 0
        self._running = True

        # Use socketio.start_background_task so the pump runs inside the
        # SocketIO event loop and can safely call socketio.emit / socketio.sleep.
        self.socketio.start_background_task(self._pump)

    def stop(self) -> None:
        """Signal the pump to exit, then stop the engine."""
        self._running = False
        if self.engine is not None:
            self.engine.stop()
            self.engine = None

    @property
    def running(self) -> bool:
        return self._running and self.engine is not None and self.engine.running

    # ── internal pump ──────────────────────────────────────────────────

    def _pump(self) -> None:
        """
        Continuously pull frames from the engine and emit them over the
        WebSocket as ``sonar_data`` events until ``stop()`` is called.

        Runs inside the SocketIO background task executor so that
        ``socketio.emit`` and ``socketio.sleep`` are safe to call here.
        """
        while self._running and self.engine is not None and self.engine.running:
            frame = self.engine.get_frame(timeout=0.1)

            if frame is None:
                # No frame yet — yield and retry without blocking the loop
                self.socketio.sleep(0)
                continue

            self._frame_count += 1

            maxdist = self.engine.maxdist
            Nplot   = self.engine.Nplot

            # ── Peak detection ─────────────────────────────────────────
            # frame[0] is always 1.0 (the normalised direct-path peak).
            # Skip the first 10 % of bins to avoid the direct-path
            # artefact, then only report if the peak is above a noise
            # threshold so the readout stays quiet when nothing is
            # in range.
            SKIP   = max(1, Nplot // 10)          # ignore first 10 %
            THRESH = 0.25                          # min normalised amplitude

            search  = frame[SKIP:]
            peak_in = int(np.argmax(search))
            peak_v  = float(search[peak_in])

            if peak_v >= THRESH:
                peak_idx  = SKIP + peak_in
                peak_dist = float(peak_idx / Nplot * maxdist)
                # Exponential moving average — reduces jitter while tracking
                # real target motion (alpha=0.35: higher = faster response)
                EMA = 0.35
                if self._smoothed_dist == 0.0:
                    self._smoothed_dist = peak_dist
                else:
                    self._smoothed_dist = EMA * peak_dist + (1 - EMA) * self._smoothed_dist
                out_dist = round(self._smoothed_dist, 1)
            else:
                peak_dist = 0.0          # no confident detection
                self._smoothed_dist = 0.0
                out_dist = 0.0

            # ── Display normalisation ──────────────────────────────────
            # Exclude DC bin (index 0) + zero the direct-path region
            # (first SKIP bins) so the red artefact at the low end
            # disappears. Normalise by 97th percentile of the valid range.
            display = frame[1:].copy()
            display[:SKIP - 1] = 0.0    # mask out direct-path artefact
            p97 = float(np.percentile(display[SKIP - 1:], 97))
            if p97 > 1e-6:
                display = np.clip(display / p97, 0.0, 1.0) ** 0.6
            else:
                display = np.zeros_like(display)

            payload = {
                "frame":       display.tolist(),   # normalised, DC + direct-path excluded
                "peak_dist":   out_dist,
                "peak_val":    round(peak_v, 4),
                "mic_level":   round(self._mic_level, 4),
                "frame_count": self._frame_count,
            }
            self.socketio.emit("sonar_data", payload)

            # Yield after each emit so the server stays responsive
            self.socketio.sleep(0)
