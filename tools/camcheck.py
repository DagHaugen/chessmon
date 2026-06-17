"""Non-interactive webcam check: open a camera, grab one frame, save a snapshot.

Usage:  python tools/camcheck.py [index]

Tries DirectShow, then Media Foundation, then any backend. Writes out/camN.png
so the frame can be inspected without an interactive GUI window.
"""
import os
import sys
import time

import cv2

BACKENDS = [(cv2.CAP_DSHOW, "DSHOW"), (cv2.CAP_MSMF, "MSMF"), (cv2.CAP_ANY, "ANY")]


def grab(idx):
    for backend, name in BACKENDS:
        cap = cv2.VideoCapture(idx, backend)
        opened = cap.isOpened()
        frame = None
        if opened:
            for _ in range(15):                 # warm-up: first reads are often empty
                ok, f = cap.read()
                if ok and f is not None and f.size > 0:
                    frame = f
                    break
                time.sleep(0.06)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        print(f"  index {idx} [{name}]: opened={opened} "
              f"frame={'yes' if frame is not None else 'no'} size={w}x{h} fps={fps:.0f}")
        if frame is not None:
            return frame, name
    return None, None


def main():
    idxs = [int(sys.argv[1])] if len(sys.argv) > 1 else [0, 1, 2]
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    os.makedirs(out_dir, exist_ok=True)
    print(f"OpenCV {cv2.__version__}; probing camera indices {idxs}")
    for idx in idxs:
        frame, backend = grab(idx)
        if frame is not None:
            path = os.path.join(out_dir, f"cam{idx}.png")
            cv2.imwrite(path, frame)
            print(f"OK: camera {idx} via {backend}, {frame.shape[1]}x{frame.shape[0]} -> {path}")
            return 0
    print("FAIL: no camera produced a frame.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
