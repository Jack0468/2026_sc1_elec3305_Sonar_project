# Import functions and libraries
import numpy as np
import matplotlib.cm as cm
from scipy import signal
from scipy import interpolate
from numpy import *
import threading, time, queue, pyaudio
import sys

from IPython.display import clear_output

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtWidgets


def put_data( Qout, ptrain, Twait, stop_flag):
    while( not stop_flag.is_set() ):
        if ( Qout.qsize() < 2 ):
            Qout.put( ptrain )
            
        time.sleep(Twait)
            
    Qout.put("EOT")
            
def play_audio( Qout, p, fs, stop_flag, dev=None):
    # open output stream
    ostream = p.open(format=pyaudio.paFloat32, channels=1, rate=int(fs),output=True,output_device_index=dev)
    # play audio
    while ( not stop_flag.is_set()):
        data = Qout.get()
        if str(data) == "EOT" :
            break
        try:
            ostream.write( data.astype(np.float32).tobytes() )
        except:
            break
    ostream.stop_stream()
    ostream.close()
            
def record_audio( Qin, p, fs, stop_flag, dev=None,chunk=2048):
    istream = p.open(format=pyaudio.paFloat32, channels=1, rate=int(fs),input=True,input_device_index=dev,frames_per_buffer=chunk)

    # record audio in chunks and append to frames
    ct = 0
    frames = []
    while (  not stop_flag.is_set() ):
        try:  # when the pyaudio object is destroyed, stops
            data_str = istream.read(chunk,exception_on_overflow=False) # read a chunk of data
            ct += 1
        except:
            print("Count is ",ct)
            print("Unexpected error:", sys.exc_info()[0])
            break
        
        data_flt = np.frombuffer( data_str, 'float32' ) # convert string to float
        
        # ---> DEBUG PRINT STATEMENT <---
        clear_output(wait=True)
        print(f"Mic Level: {np.max(np.abs(data_flt)):.5f}      ", end='\r')
        
        Qin.put( data_flt ) # append to list
        
    istream.stop_stream()
    istream.close()
    Qin.put("EOT")

    
def signal_process( Qin, Qdata, pulse_a, Nseg, Nplot, fs, maxdist, temperature, functions, stop_flag, dev=None ):
    crossCorr = functions[2]
    findDelay = functions[3]
    dist2time = functions[4]
    
    # initialize Xrcv 
    Xrcv = np.zeros( 3 * Nseg, dtype='complex' )
    cur_idx = 0 # keeps track of current index
    found_delay = False
    
    # Use np.minimum to avoid AxisError
    maxsamp = np.minimum(int(dist2time( maxdist, temperature) * fs), Nseg) 
    
    while( not stop_flag.is_set() ):
        # Get streaming chunk
        chunk = Qin.get()
        if (str(chunk) == "EOT"):
            break
        Xchunk = crossCorr( chunk, pulse_a ) 
        Xchunk = np.reshape(Xchunk,(1,len(Xchunk)))
        
        # Overlap-and-add
        try:
            Xrcv[cur_idx:(cur_idx+len(chunk)+len(pulse_a)-1)] += Xchunk[0,:]
        except:
            pass
            
        cur_idx += len(chunk)
        if( found_delay and (cur_idx >= Nseg) ):
            if found_delay:
                idx = findDelay( abs(Xrcv), Nseg )
                Xrcv = np.roll(Xrcv, -idx )
                Xrcv[-idx:] = 0
                cur_idx = cur_idx - idx
            
            # crop a segment from Xrcv and interpolate to Nplot
            Xrcv_seg = (abs(Xrcv[:maxsamp].copy()) / np.maximum(abs( Xrcv[0] ),1e-5) ) ** 0.5    
            
            # ---> FIX: Changed r_ to np.arange to prevent silent NameError crashing the thread <---
            x_old = np.arange(maxsamp)
            interp = interpolate.interp1d(x_old, Xrcv_seg)
            
            x_new = np.linspace(0, maxsamp - 1, Nplot)
            Xrcv_seg = interp(x_new)
            
            # remove segment from Xrcv
            Xrcv = np.roll(Xrcv, -Nseg )
            Xrcv[-Nseg:] = 0
            cur_idx = cur_idx - Nseg
            
            Qdata.put( Xrcv_seg )
            
        elif( cur_idx > 2 * Nseg ):
            # Uses two pulses to calculate delay
            idx = findDelay( abs(Xrcv), Nseg )
            Xrcv = np.roll(Xrcv, -idx )
            Xrcv[-idx:] = 0
            cur_idx = cur_idx - idx - 1
            found_delay = True
             
    Qdata.put("EOT")


def image_update( Qdata, img_item, plot_item_1d, Nrep, Nplot, stop_flag ):
    # Create a native PyQtGraph image array: (Width/X, Height/Y, RGBA)
    img = np.zeros((Nplot, Nrep, 4), dtype=np.uint8)
    
    # Pre-generate an X-axis representing distance in cm up to maxdist
    # img_item coordinates are mapped via setRect later
    x_axis = np.linspace(0, 200, Nplot) # Hardcoded to maxdist default for line plot matching

    while( not stop_flag.is_set() ):
        new_line = Qdata.get()
        if str(new_line) == "EOT" :
            break
        
        # --- UPDATE 1D MATCHED FILTER PEAK PLOT ---
        # Scale the amplitude slightly for visibility on a 0-30 scale
        plot_item_1d.setData(x_axis, new_line * 15)
        
        # --- WATERFALL LOGIC ---
        new_line_norm = np.minimum(new_line/np.maximum(np.percentile(new_line, 97), 1e-5), 1)**(1/1.8)
        
        # Roll the image along the Y-axis (axis=1) to scroll the waterfall upwards
        img = np.roll(img, 1, axis=1)
        
        # Apply color map and assign to the bottom row (Y=0)
        img[:, 0, :] = (cm.jet(new_line_norm) * 255).astype(np.uint8)
        
        # Update the image
        img_item.setImage(img, autoLevels=False)
        
        print(f"Plot Data Ready! Peak: {np.max(new_line):.2f}      ", end='\r')
        
        # ---> FIX: Removed Qdata.queue.clear() to prevent frame-drop thread synchronization locks <---
        
        # CHECK input_device_index = 0 ?
def rtsonar( f0, f1, fs, Npulse, Nseg, Nrep, Nplot, maxdist, temperature, functions, input_device_index=None ):

    clear_output()
    genChirpPulse = functions[0]
    genPulseTrain = functions[1]
    
    pulse_a = genChirpPulse(Npulse, f0, f1, fs)
    hanWin = np.hanning(Npulse)
    hanWin = np.reshape(hanWin, (Npulse, 1))
    pulse_a = np.multiply(pulse_a, hanWin)
    pulse = np.real(pulse_a)
    ptrain = genPulseTrain(pulse, Nrep, Nseg)
    
    # Create input/output FIFO queues
    Qin = queue.Queue()
    Qout = queue.Queue()
    Qdata = queue.Queue()

    # Create a pyaudio object
    p = pyaudio.PyAudio()
    
    # 1. PyQtGraph: Create Qt App and Window Layout
    app = pg.mkQApp("Sonar App")
    win = pg.GraphicsLayoutWidget(show=True, title="Real-Time Sonar System")
    win.resize(1200, 500) # Widened window to fit both displays side-by-side
    
    # 2. PANEL 1: Configure Waterfall Plot
    plot_waterfall = win.addPlot(title="Sonar History (Waterfall)", labels={'left': 'Time [s]', 'bottom': 'Distance [cm]'})
    y_max_time = (Nrep * Nseg) / fs
    plot_waterfall.setXRange(0, maxdist, padding=0)
    plot_waterfall.setYRange(0, y_max_time, padding=0)
    plot_waterfall.setLimits(xMin=0, xMax=maxdist, yMin=0, yMax=y_max_time)
    plot_waterfall.getViewBox().setMouseEnabled(x=False, y=False) 

    img_item = pg.ImageItem()
    plot_waterfall.addItem(img_item)
    img_item.setRect(QtCore.QRectF(0, 0, maxdist, y_max_time))

    # 3. PANEL 2: Configure 1D Matched Filter Peak Plot (Placed right next to waterfall)
    plot_peaks = win.addPlot(title="Real-Time Matched Filter Output", labels={'left': 'Magnitude', 'bottom': 'Distance [cm]'})
    plot_peaks.setXRange(0, maxdist, padding=0)
    plot_peaks.setYRange(0, 30) # Adjust maximum magnitude ceiling depending on mic sensitivity
    plot_peaks.setLimits(xMin=0, xMax=maxdist, yMin=0)
    
    # Create a clean yellow line curve for the live peaks
    curve_item = plot_peaks.plot(pen=pg.mkPen('y', width=2))

    # Initialize stop_flag
    stop_flag = threading.Event()

    # Initialize processing and hardware threads
    t_put_data = threading.Thread(target = put_data,   args = (Qout, ptrain, Nseg / fs*3, stop_flag  ))
    t_rec = threading.Thread(target = record_audio,   args = (Qin, p, fs, stop_flag, input_device_index  ))
    t_play_audio = threading.Thread(target = play_audio,   args = (Qout, p, fs, stop_flag ))
    t_signal_process = threading.Thread(target = signal_process, args = ( Qin, Qdata, pulse_a, Nseg, Nplot, fs, maxdist, temperature, functions, stop_flag, input_device_index  ))
    
    # NEW: Pass curve_item down to thread alongside img_item
    t_image_update = threading.Thread(target = image_update, args = (Qdata, img_item, curve_item, Nrep, Nplot, stop_flag ) )

    # Start all background operations
    t_put_data.start()
    t_rec.start()
    t_play_audio.start()
    t_signal_process.start()
    t_image_update.start()

    return stop_flag, win