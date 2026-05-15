import threading, time, queue, pyaudio
import numpy as np
from threading import Lock

# ==============================================================================
# --- NOTEBOOK FIXES ---
# These functions are meant to be copy-pasted into the respective cells in the 
# notebooks to fix the issues mentioned in the comments. 
# this is not a functional module, but a collection of code snippets for fixing the notebooks.

# ==============================================================================
# --- CELL 1: AUDIO TRANSCEIVER FIXES (Run this cell first) ---
# Fixes ALSA "underrun occurred" and Core Dump crashes by closing streams safely
# ==============================================================================
def play_audio(Q, p, fs, dev=None):
    ostream = p.open(format=pyaudio.paFloat32, channels=1, rate=int(fs), output=True, output_device_index=dev)
    
    while True:
        data = Q.get()
        if isinstance(data, str) and data == "EOT":
            break
        try:
            ostream.write(data.astype(np.float32).tobytes())
        except:
            break
            
    ostream.stop_stream()
    ostream.close()

def record_audio(queue_in, p, fs, dev=None, chunk=2048, lock=None, record_secs=None):
    istream = p.open(format=pyaudio.paFloat32, channels=1, rate=int(fs), input=True, input_device_index=dev, frames_per_buffer=chunk)

    max_chunks = int(record_secs * fs / chunk) + 5 if record_secs else None
    chunks_read = 0
    
    while True:
        if max_chunks and chunks_read >= max_chunks:
            break
        try:
            if lock is not None:
                with lock:
                    data_str = istream.read(chunk, exception_on_overflow=False)
            else:
                data_str = istream.read(chunk, exception_on_overflow=False)
        except:
            break
            
        data_flt = np.frombuffer(data_str, dtype='float32')
        queue_in.put(data_flt)
        chunks_read += 1
        
    istream.stop_stream()
    istream.close()

def xciever(sig, fs):
    rcv = []
    Qin = queue.Queue()
    Qout = queue.Queue()
    lock = Lock()
    p = pyaudio.PyAudio()

    RECORD_SECS = len(sig)/fs + 2.0
    
    t_rec = threading.Thread(target=record_audio, args=(Qin, p, fs), kwargs={'lock': lock, 'record_secs': RECORD_SECS})
    t_play_audio = threading.Thread(target=play_audio, args=(Qout, p, fs))

    t_rec.daemon = True
    t_play_audio.daemon = True

    t_rec.start()
    t_play_audio.start()

    Qout.put(sig)
    Qout.put("EOT")

    try:
        while t_play_audio.is_alive() or t_rec.is_alive():
            t_play_audio.join(0.1)
            t_rec.join(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        with lock:
            p.terminate()

    chunks = []
    while not Qin.empty():
        chunks.append(Qin.get())
    rcv = np.concatenate(chunks) if chunks else np.array([])

    return rcv



# ==============================================================================
# --- CELL 2: BROADCASTING FIX (Run this cell after Cell 1) ---
# Fixes ValueError: could not broadcast input array from shape (72,72)
# ==============================================================================
def sortOfASonar(Npulse, f0, f1,fs, Nrep, Nseg):
    # Flatten chirp to 1D to prevent broadcasting errors
    pulse_a = np.ravel(genChirpPulse(Npulse, f0, f1, fs))
    hanWin = np.hanning(Npulse)
    
    # Multiply standard 1D arrays
    pulse_a = pulse_a * hanWin
    pulse = np.real(pulse_a)
    
    ptrain = genPulseTrain(pulse, Nrep, Nseg)
    rcv = xciever(ptrain/2.0 , fs) 
    Xrcv_a = abs( crossCorr(rcv, pulse_a) )
    Xrcv_a = np.reshape(Xrcv_a, (1,len(Xrcv_a)))
    
    idx = findDelay(Xrcv_a,Nseg) 
    img = np.zeros((Nrep,Nseg))
    img[0,:] = Xrcv_a[0,idx:idx+Nseg]
    
    # Look for peak in each pulse in the pulse train to avoid drift
    for n in range(1,Nrep):
       idxx = findDelay(Xrcv_a[0,idx+int(Nseg/2):idx+int(Nseg/2)+Nseg],Nseg)
       idx = idx + idxx + int(Nseg/2)
       img[n,:]=Xrcv_a[0,idx:idx+Nseg]
        
    return img