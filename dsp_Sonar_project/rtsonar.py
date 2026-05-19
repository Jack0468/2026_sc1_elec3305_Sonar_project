# rtsonar.py
# ==========
# Real‑time sonar visualisation using PyQtGraph.
#
# This module provides a thin GUI layer on top of ``sonar_core.SonarEngine``.
# All DSP / audio work is done by the engine; this file only handles
# PyQtGraph rendering on the main (GUI) thread via a QTimer.

import numpy as np
import matplotlib.cm as cm

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets

from sonar_core.engine import SonarEngine


def rtsonar(f0, f1, fs, Npulse, Nseg, Nrep, Nplot, maxdist, temperature,
            functions=None, input_device_index=None, output_device_index=None):
    """
    Launch a real‑time sonar display.

    Parameters
    ----------
    f0, f1 : float
        Chirp start / end frequencies (Hz).
    fs : float
        Sampling rate (Hz).
    Npulse : int
        Chirp pulse length (samples).
    Nseg : int
        Segment length (samples between pulses).
    Nrep : int
        Number of repetitions in the pulse train.
    Nplot : int
        Horizontal resolution of the display.
    maxdist : float
        Maximum detection distance (cm).
    temperature : float
        Ambient temperature (°C).
    functions : tuple or None
        Legacy parameter — ignored. DSP functions come from sonar_core.dsp.
        Kept for backward compatibility with the original notebook signature.
    input_device_index : int or None
        PyAudio input (microphone) device index. None = system default.
    output_device_index : int or None
        PyAudio output (speaker) device index. None = system default.

    Returns
    -------
    stop_flag : threading.Event
        Set this to stop the sonar:  ``stop_flag.set()``
    win : pg.GraphicsLayoutWidget
        The Qt window. **You must keep a reference to this** — the QTimer
        that drives the display is parented to ``win``, so if ``win`` is
        garbage‑collected the display will stop updating.
    """

    # ── diagnostic: list devices so misrouted audio is obvious ────────
    import pyaudio
    _p = pyaudio.PyAudio()
    print("── Audio devices ──────────────────────────────────────")
    for i in range(_p.get_device_count()):
        info = _p.get_device_info_by_index(i)
        tags = []
        if info["maxInputChannels"] > 0:
            tags.append(f"IN ch={info['maxInputChannels']}")
        if info["maxOutputChannels"] > 0:
            tags.append(f"OUT ch={info['maxOutputChannels']}")
        marker = ""
        if i == input_device_index:
            marker += " ← MIC"
        if i == output_device_index:
            marker += " ← SPEAKER"
        if info["maxInputChannels"] > 0 and input_device_index is None and \
                i == _p.get_default_input_device_info()["index"]:
            marker += " ← default MIC"
        if info["maxOutputChannels"] > 0 and output_device_index is None and \
                i == _p.get_default_output_device_info()["index"]:
            marker += " ← default SPEAKER"
        print(f"  [{i}] {info['name']}  {', '.join(tags)}{marker}")
    _p.terminate()
    print("───────────────────────────────────────────────────────")
    print(f"Starting sonar:  f0={f0} Hz  f1={f1} Hz  Npulse={Npulse}"
          f"  Nseg={Nseg}  maxdist={maxdist} cm")

    # ── build engine ───────────────────────────────────────────────────
    engine = SonarEngine(
        f0=f0, f1=f1, fs=fs,
        Npulse=Npulse, Nseg=Nseg, Nrep=Nrep, Nplot=Nplot,
        maxdist=maxdist, temperature=temperature,
        input_device_index=input_device_index,
        output_device_index=output_device_index,
    )

    # ── Qt app & window ────────────────────────────────────────────────
    app = pg.mkQApp("Sonar App")  # returns existing QApp if %gui qt is active
    win = pg.GraphicsLayoutWidget(title="Real-Time Sonar System")
    win.resize(1200, 500)
    win.show()

    # Panel 1 — waterfall
    plot_waterfall = win.addPlot(
        title="Sonar History (Waterfall)",
        labels={"left": "Time [s]", "bottom": "Distance [cm]"},
    )
    y_max_time = (Nrep * Nseg) / fs
    plot_waterfall.setXRange(0, maxdist, padding=0)
    plot_waterfall.setYRange(0, y_max_time, padding=0)
    plot_waterfall.setLimits(xMin=0, xMax=maxdist, yMin=0, yMax=y_max_time)
    plot_waterfall.getViewBox().setMouseEnabled(x=False, y=False)

    img_item = pg.ImageItem()
    plot_waterfall.addItem(img_item)
    img_item.setRect(QtCore.QRectF(0, 0, maxdist, y_max_time))

    # Panel 2 — matched‑filter peaks
    plot_peaks = win.addPlot(
        title="Real-Time Matched Filter Output",
        labels={"left": "Magnitude", "bottom": "Distance [cm]"},
    )
    plot_peaks.setXRange(0, maxdist, padding=0)
    plot_peaks.setYRange(0, 30)
    plot_peaks.setLimits(xMin=0, xMax=maxdist, yMin=0)
    curve_item = plot_peaks.plot(pen=pg.mkPen("y", width=2))

    # Waterfall image buffer: (X=distance pixels, Y=time history, RGBA)
    img = np.zeros((Nplot, Nrep, 4), dtype=np.uint8)

    # x‑axis for the 1‑D plot — uses the actual maxdist parameter
    x_axis = np.linspace(0, maxdist, Nplot)

    # ── timer‑driven GUI update (runs on the Qt/GUI thread) ───────────
    def _update():
        nonlocal img
        frame = engine.get_frame(timeout=0.02)
        if frame is None:
            return

        # 1‑D matched‑filter line plot
        curve_item.setData(x_axis, frame * 15)

        # Waterfall: normalise and map through jet colourmap
        norm = np.minimum(
            frame / max(np.percentile(frame, 97), 1e-5), 1
        ) ** (1 / 1.8)

        img[:] = np.roll(img, 1, axis=1)
        img[:, 0, :] = (cm.jet(norm) * 255).astype(np.uint8)
        img_item.setImage(img, autoLevels=False)

    # ── IMPORTANT: parent the timer to `win` ──────────────────────────
    # A local QTimer is garbage‑collected when rtsonar() returns, which
    # immediately stops _update() from ever firing.  Parenting it to win
    # ties its lifetime to the window the caller keeps a reference to.
    timer = QtCore.QTimer(parent=win)
    timer.timeout.connect(_update)
    timer.start(30)  # ~33 fps

    # ── start the audio engine ────────────────────────────────────────
    engine.start()
    print("Sonar engine started — window should appear now.")
    print("To stop:  stop_flag.set()")

    return engine.stop_flag, win