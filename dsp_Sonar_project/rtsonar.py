# Import functions and libraries
import numpy as np
import matplotlib.cm as cm
from scipy import signal
from scipy import interpolate
from numpy import *
import threading, time, queue, pyaudio
import bokeh.plotting as bk
from bokeh.resources import INLINE
from bokeh.models import GlyphRenderer
from bokeh.io import push_notebook, output_notebook
from IPython.display import clear_output
import sys

# Load BokehJS once at import time
output_notebook(INLINE, hide_banner=True)


def put_data(Qout, ptrain, Twait, stop_flag):
    while not stop_flag.is_set():
        if Qout.qsize() < 2:
            Qout.put(ptrain)
        time.sleep(Twait)
    Qout.put("EOT")


def play_audio(Qout, ostream, stop_flag):
    print("[play] playback thread running", flush=True)
    while not stop_flag.is_set():
        data = Qout.get()
        if isinstance(data, str) and data == "EOT":
            break
        try:
            ostream.write(data.astype(np.float32).tobytes(), exception_on_underflow=False)
        except Exception as e:
            print(f"[play] write error: {e}", flush=True)
            break
            
    try:
        ostream.stop_stream()
        ostream.close()
    except Exception as e:
        print(f"[play] stream cleanup error (ignoring): {e}", flush=True)


def record_audio(Qin, istream, stop_flag, chunk=2048):
    print("[rec] recorder thread running", flush=True)
    last_report = time.time()
    peak_since = 0.0
    while not stop_flag.is_set():
        try:
            data_str = istream.read(chunk, exception_on_overflow=False)
        except Exception as e:
            print(f"[rec] read error: {e}", flush=True)
            break
        data_flt = np.frombuffer(data_str, 'float32')

        cur_peak = float(np.max(np.abs(data_flt))) if data_flt.size else 0.0
        if cur_peak > peak_since:
            peak_since = cur_peak
        now = time.time()
        if now - last_report >= 1.0:
            status = "OK" if peak_since > 1e-4 else "SILENT (mic blocked / wrong device / permission?)"
            print(f"[mic] peak={peak_since:.5f}  {status}", flush=True)
            sys.stdout.flush()
            last_report = now
            peak_since = 0.0

        Qin.put(data_flt)
        
    try:
        istream.stop_stream()
        istream.close()
    except Exception as e:
        print(f"[rec] stream cleanup error (ignoring): {e}", flush=True)
        
    Qin.put("EOT")
    print("[rec] recorder stopped", flush=True)


def signal_process(Qin, Qdata, pulse_a, Nseg, Nplot, fs, maxdist, temperature, functions, stop_flag):
    crossCorr = functions[2]
    findDelay = functions[3]
    dist2time = functions[4]

    Xrcv = zeros(3 * Nseg, dtype='complex')
    cur_idx = 0
    found_delay = False

    maxsamp = int(np.minimum(int(dist2time(maxdist, temperature) * fs), Nseg))

    while not stop_flag.is_set():
        chunk = Qin.get()
        if isinstance(chunk, str) and chunk == "EOT":
            break
        Xchunk = crossCorr(chunk, pulse_a)
        Xchunk = np.reshape(Xchunk, (1, len(Xchunk)))

        try:
            Xrcv[cur_idx:(cur_idx + len(chunk) + len(pulse_a) - 1)] += Xchunk[0, :]
        except Exception as e:
            print(f"[signal] overlap-add error: {e}", flush=True)

        cur_idx += len(chunk)

        if found_delay and (cur_idx >= Nseg):
            idx = findDelay(abs(Xrcv), Nseg)
            if idx > 0:
                Xrcv = np.roll(Xrcv, -idx)
                Xrcv[-idx:] = 0
                cur_idx -= idx

            Xrcv_seg = (abs(Xrcv[:maxsamp].copy()) / np.maximum(abs(Xrcv[0]), 1e-5)) ** 0.5
            interp = interpolate.interp1d(r_[:maxsamp], Xrcv_seg)
            Xrcv_seg = interp(r_[:maxsamp - 1:(Nplot * 1j)])

            Xrcv = np.roll(Xrcv, -Nseg)
            Xrcv[-Nseg:] = 0
            cur_idx -= Nseg

            Qdata.put(Xrcv_seg)

        elif cur_idx > 2 * Nseg:
            idx = findDelay(abs(Xrcv), Nseg)
            Xrcv = np.roll(Xrcv, -idx)
            Xrcv[-idx:] = 0
            cur_idx = cur_idx - idx - 1
            found_delay = True

    Qdata.put("EOT")


def image_update(Qdata, fig, Nrep, Nplot, stop_flag, handle):
    renderer = fig.select(dict(name='echos', type=GlyphRenderer))
    source = renderer[0].data_source
    img = source.data['image'][0]

    while not stop_flag.is_set():
        new_line = Qdata.get()
        if isinstance(new_line, str) and new_line == "EOT":
            break

        new_line = np.minimum(new_line / np.maximum(np.percentile(new_line, 97), 1e-5), 1) ** (1 / 1.8)
        img = np.roll(img, 1, 0)
        view = img.view(dtype=np.uint8).reshape((Nrep, Nplot, 4))
        view[0, :, :] = cm.jet(new_line) * 255

        source.data['image'] = [img]
        if handle is not None:
            try:
                push_notebook(handle=handle)
            except Exception:
                pass

        with Qdata.mutex:
            Qdata.queue.clear()

## note that in_dev and out_dev are device indices for pyaudio, not necessarily the same as sounddevice. 
# Check with p.get_device_info_by_index() to find the right ones for your system.
def rtsonar(f0, f1, fs, Npulse, Nseg, Nrep, Nplot, maxdist, temperature, functions, in_dev=1, out_dev=1):
    clear_output()
    output_notebook(INLINE, hide_banner=True)

    genChirpPulse = functions[0]
    genPulseTrain = functions[1]

    pulse_a = genChirpPulse(Npulse, f0, f1, fs)
    hanWin = np.hanning(Npulse).reshape(Npulse, 1)
    pulse_a = np.multiply(pulse_a, hanWin)
    pulse = np.real(pulse_a)
    ptrain = genPulseTrain(pulse, Nrep, Nseg)

    Qin = queue.Queue()
    Qout = queue.Queue()
    Qdata = queue.Queue()

    p = pyaudio.PyAudio()

    # Open both audio streams in the main thread to avoid CoreAudio hanging
    # when input and output streams are opened concurrently from threads.
    chunk = 2048
    print(f"[init] opening input stream (device={in_dev})...", flush=True)
    istream = p.open(format=pyaudio.paFloat32, channels=1, rate=int(fs),
                     input=True, input_device_index=in_dev, frames_per_buffer=chunk)
    print("[init] input stream open", flush=True)
    print(f"[init] opening output stream (device={out_dev})...", flush=True)
    ostream = p.open(format=pyaudio.paFloat32, channels=1, rate=int(fs),
                     output=True, output_device_index=out_dev, frames_per_buffer=chunk)
    print("[init] output stream open", flush=True)

    img = np.zeros((Nrep, Nplot), dtype=np.uint32)
    view = img.view(dtype=np.uint8).reshape((Nrep, Nplot, 4))
    view[:, :, 3] = 255

    fig = bk.figure(title='Sonar', y_axis_label="Time [s]", x_axis_label="Distance [cm]",
                    x_range=(0, maxdist), y_range=(0, Nrep * Nseg / fs),
                    height=400, width=800)
    fig.image_rgba(image=[img], x=[0], y=[0], dw=[maxdist], dh=[Nrep * Nseg / fs], name='echos')

    print("[init] rendering via classic bk.show + push_notebook", flush=True)
    handle = bk.show(fig, notebook_handle=True)

    stop_flag = threading.Event()

    t_put_data = threading.Thread(target=put_data, args=(Qout, ptrain, Nseg / fs * 3, stop_flag))
    t_rec = threading.Thread(target=record_audio, args=(Qin, istream, stop_flag, chunk))
    t_play_audio = threading.Thread(target=play_audio, args=(Qout, ostream, stop_flag))
    t_signal_process = threading.Thread(target=signal_process,
                                        args=(Qin, Qdata, pulse_a, Nseg, Nplot, fs, maxdist, temperature, functions, stop_flag))
    t_image_update = threading.Thread(target=image_update, args=(Qdata, fig, Nrep, Nplot, stop_flag, handle))

    t_put_data.start()
    t_rec.start()
    t_play_audio.start()
    t_signal_process.start()
    t_image_update.start()

    return stop_flag