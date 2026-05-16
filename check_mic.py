import pyaudio

p = pyaudio.PyAudio()
print("Available Audio Devices:")
for i in range(p.get_device_count()):
    dev = p.get_device_info_by_index(i)
    print(f"Index {i}: {dev['name']} (In: {dev['maxInputChannels']}, Out: {dev['maxOutputChannels']})")
p.terminate()
