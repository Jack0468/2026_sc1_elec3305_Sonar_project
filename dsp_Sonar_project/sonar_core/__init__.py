# sonar_core - Shared DSP engine for the Sonar project
#
# This package provides the core signal processing functions and
# real-time sonar engine used by both the PyQtGraph desktop application
# and the Flask web application.

from sonar_core.dsp import (
    genChirpPulse,
    genPulseTrain,
    crossCorr,
    findDelay,
    dist2time,
    time2dist,
)
try:
    from sonar_core.engine import SonarEngine
except (ImportError, OSError):
    SonarEngine = None

__all__ = [
    "genChirpPulse",
    "genPulseTrain",
    "crossCorr",
    "findDelay",
    "dist2time",
    "time2dist",
    "SonarEngine",
]
