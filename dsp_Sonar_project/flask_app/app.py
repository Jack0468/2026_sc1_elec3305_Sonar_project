"""
flask_app.app
=============

Flask + SocketIO application for the Real‑Time Sonar System.

Run::

    conda activate sonar_env
    cd dsp_Sonar_project/flask_app
    python app.py

Then open http://localhost:5000 in a browser.
"""

import sys
import os
import json

# Ensure parent dir is on path for sonar_core imports
_parent = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _parent not in sys.path:
    sys.path.insert(0, _parent)

import pyaudio
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO

from sonar_bridge import SonarBridge

# ── app setup ─────────────────────────────────────────────────────────────

app = Flask(__name__)
app.config["SECRET_KEY"] = "sonar-system-2026"

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
bridge = SonarBridge(socketio)


# ── routes ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the main sonar UI."""
    return render_template("index.html")


@app.route("/api/devices")
def list_devices():
    """Return available audio input devices as JSON."""
    p = pyaudio.PyAudio()
    devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxInputChannels"] > 0:
            devices.append({
                "index": i,
                "name": info["name"],
                "channels": info["maxInputChannels"],
                "sample_rate": info["defaultSampleRate"],
            })
    p.terminate()
    return jsonify(devices)


@app.route("/api/calibration", methods=["GET"])
def get_calibration():
    """Return calibration results if they exist."""
    cal_path = os.path.join(_parent, "calibration_result.json")
    if os.path.exists(cal_path):
        with open(cal_path) as f:
            return jsonify(json.load(f))
    return jsonify({}), 404


# ── socketio events ──────────────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    print("[WS] Client connected")


@socketio.on("disconnect")
def on_disconnect():
    print("[WS] Client disconnected")


@socketio.on("start_sonar")
def on_start_sonar(params):
    """Start the sonar engine with the provided parameters."""
    print(f"[WS] start_sonar: {params}")
    bridge.start(params)
    socketio.emit("status", {"running": True})


@socketio.on("stop_sonar")
def on_stop_sonar():
    """Stop the running sonar engine."""
    print("[WS] stop_sonar")
    bridge.stop()
    socketio.emit("status", {"running": False})


@socketio.on("calibrate")
def on_calibrate(params):
    """Run auto‑calibration in a background thread."""
    from sonar_core.calibration import calibrate

    print("[WS] calibrate: starting …")
    socketio.emit("calibration_status", {"status": "running"})

    try:
        result = calibrate(
            input_dev=params.get("input_device_index"),
            output_dev=None,
            fs=float(params.get("fs", 48000)),
            output_path=os.path.join(_parent, "calibration_result.json"),
            interactive=False,
        )
        socketio.emit("calibration_status", {
            "status": "complete",
            "result": result,
        })
    except Exception as e:
        socketio.emit("calibration_status", {
            "status": "error",
            "error": str(e),
        })


# ── main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 50)
    print("  SONAR SYSTEM — Flask Server")
    print("  Open http://localhost:5000")
    print("=" * 50)
    socketio.run(app, host="127.0.0.1", port=5000, debug=False,
                 allow_unsafe_werkzeug=True)
