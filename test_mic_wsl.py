import pyaudio
import numpy as np
import time
import sys

"""
This test script attempts to record audio from the default input 
device in WSL and prints a simple visual meter of the peak volume.
Note: WSL's audio support can be inconsistent, and you may need to 
configure PulseAudio or ALSA properly for this to work.

TRY:
pactl list short sources

look for device named "SOURCE"

pactl set-default-source NAME_OF_SOURCE
""" 

def test_microphone(device_index=None, record_seconds=10, chunk_size=1024, rate=48000):
    p = pyaudio.PyAudio()
    
    # List all input devices
    print("\n--- Available Input Devices ---")
    for i in range(p.get_device_count()):
        try:
            dev = p.get_device_info_by_index(i)
            if dev.get('maxInputChannels', 0) > 0:
                print(f"Index {i}: {dev.get('name')}")
        except IOError:
            pass
    print("-------------------------------\n")

    if device_index is None:
        try:
            default_dev = p.get_default_input_device_info()
            device_index = default_dev['index']
            print(f"Using DEFAULT input device Index {device_index}: {default_dev['name']}")
        except IOError:
            print("Error: No default input device found.")
            p.terminate()
            return
    else:
        dev_info = p.get_device_info_by_index(device_index)
        print(f"Using SPECIFIED input device Index {device_index}: {dev_info['name']}")

    print(f"Recording for {record_seconds} seconds. Please make some noise...")
    
    try:
        stream = p.open(format=pyaudio.paFloat32,
                        channels=1,
                        rate=rate,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=chunk_size)
    except Exception as e:
        print(f"Failed to open audio stream. (WSL ALSA/PulseAudio issue?): {e}")
        p.terminate()
        return

    start_time = time.time()
    
    try:
        while time.time() - start_time < record_seconds:
            # exception_on_overflow=False prevents crashes from WSL/ALSA dropped frames
            data = stream.read(chunk_size, exception_on_overflow=False)
            audio_data = np.frombuffer(data, dtype=np.float32)
            
            # Calculate peak volume in this chunk
            peak_volume = np.max(np.abs(audio_data))
            
            # Print a visual meter
            bars = int(peak_volume * 100)  # Scale for visual representation
            print(f"Peak: {peak_volume:.4f} | {'#' * bars}")
            
    except Exception as e:
        print(f"Error during recording: {e}")
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()
        print("Finished testing microphone.")

if __name__ == "__main__":
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else None
    test_microphone(device_index=idx)