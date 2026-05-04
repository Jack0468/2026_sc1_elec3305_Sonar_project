# DSP Sonar Project

This project implements an acoustic sonar system using your laptop's speaker and microphone. It utilizes digital signal processing (DSP) techniques, specifically Linear Frequency Modulated (LFM) chirps and pulse compression via matched filtering, to accurately measure distances.

## What's Implemented

The core signal processing logic is implemented across the Jupyter Notebooks based on the principles outlined in the project report:

1. **Generating the Signal (Chirp):** `genChirpPulse` generates a Linear Frequency Modulated (LFM) signal, or chirp. This signal sweeps from a starting frequency ($f_0$) to an ending frequency ($f_1$) over a given duration. The advantage of a chirp is that its time-bandwidth product allows for a long, high-energy pulse that later "compresses" to a narrow time spike for very accurate resolution.
2. **Generating a Pulse Train:** `genPulseTrain` repeats the chirp pulse multiple times with a specific segment length (`Nseg`) to simulate a continuous active sonar.
3. **Cross-Correlation (Matched Filtering):** The `crossCorr` function uses `scipy.signal.fftconvolve`. This is the core of the pulse compression technique. It takes the received signal (which is messy and noisy) and correlates it against the *exact* chirp signal we sent. When the received echo aligns with our template, it produces a massive, sharp peak in the output.
4. **Finding the Delay:** `findDelay` finds the index of the massive first peak in the matched filter output. Because your laptop’s speaker and microphone are physically close, the *first* loud signal picked up by the microphone is the direct sound traveling from the speaker straight to the mic (the feedthrough). We use this as our "time zero."
5. **Distance & Time Conversion:** `dist2time` and `time2dist` calculate the distance. The speed of sound depends on the ambient temperature. These functions use the formula $v_s \approx 331.3 \times \sqrt{1 + T/273.15}$ m/s to convert the time delay ($\Delta t$) into distance, dividing by 2 since the sound travels *to* the object and *back*.

## How to Use It to Detect Distance

You will mainly be using the **`TimeDomain-RealTime-Sonar.ipynb`** notebook, which ties everything together into a live, real-time application using `pyaudio` and `bokeh` for plotting.

### Setup Requirements

1. **Hardware:** You need a laptop with a speaker and a microphone. For the best results, it's highly recommended to **use a single external speaker and a dedicated microphone** placed side-by-side facing the same direction. If using the laptop's built-in hardware, go to your OS sound settings and shift the balance to use only one speaker (the one closest to the microphone).
2. **Environment:** A quiet room is best. Hard, flat surfaces (like a wall or a whiteboard) reflect sound the best. Soft materials (like pillows or blankets) absorb sound and won't show up well.
3. **Disable Audio Enhancements:** This is critical. Modern OSs (Windows/macOS) apply noise cancellation and echo suppression to microphones. You *must* disable these "enhancements" in your system sound settings, otherwise, the OS will actively delete the sonar echoes before Python even sees them.

### Running the Live Sonar

1. **Open the Notebook:** Open `dsp_Sonar_project/TimeDomain-RealTime-Sonar.ipynb` in Jupyter Notebook or JupyterLab.
2. **Run Initialization:** Run the first few cells to import the libraries and load the signal processing functions.
3. **Initialize Real-Time Script:** Run the cell that imports/defines the real-time threading code (`rtsonar.py`).
4. **Execute the Live Sonar:** Run the final cell that configures parameters and calls `rtsonar(...)`:
   ```python
   # Example parameters
   fs = 48000
   f0 = 8000
   f1 = 8000 # (or change to a sweep like 2000 to 8000)
   Npulse = 96
   # ...
   stop_flag = rtsonar(f0, f1, fs, Npulse, Nseg, Nrep, Nplot, maxdist, temperature, functions)
   ```
5. **Observe the Plot:** 
   - A live interactive Bokeh plot will appear. 
   - The **X-axis** represents distance (in cm). The **Y-axis** represents time (history of pulses).
   - You should see a very bright vertical line near `0 cm`. This is the direct feedthrough from the speaker to the mic.
   - Point your setup at a wall and move back and forth. You should see a fainter vertical line (or curve, if you are moving) moving left and right on the plot. That is the echo reflecting off the wall! The X-axis value where that faint line appears is the distance to the wall.
6. **Stop the Sonar:** To cleanly stop the audio streams, run a cell with:
   ```python
   stop_flag.set()
   ```

*Pro Tip: If the plot is completely random noise, ensure your speaker volume is up (around 70-80%), the microphone isn't muted, and that the audio enhancements are fully disabled in your operating system.*
