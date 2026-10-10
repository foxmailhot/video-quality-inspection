import av
import numpy as np

with av.open('qc-demo.mp4', 'w') as output:
    stream = output.add_stream('libx264', rate=25)
    stream.width = 320
    stream.height = 180
    stream.pix_fmt = 'yuv420p'
    for index in range(150):
        rgb = np.zeros((180, 320, 3), dtype=np.uint8)
        rgb[:, :, 0] = (index * 3) % 255
        rgb[:, :, 1] = np.arange(320, dtype=np.uint16)[None, :] % 255
        rgb[:, :, 2] = 110
        frame = av.VideoFrame.from_ndarray(rgb, format='rgb24')
        for packet in stream.encode(frame):
            output.mux(packet)
    for packet in stream.encode():
        output.mux(packet)
