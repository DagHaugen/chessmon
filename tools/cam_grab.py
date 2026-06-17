"""Grab one frame from a camera and save it (for hands-on board testing).

    python tools/cam_grab.py [index] [label] [width] [height]

Defaults: index 0, label "shot", 1280x720 (DirectShow). Saves out/<label>.png.
Use this to snap an empty board, a start position, etc., then run the offline
detector on the saved images.
"""
import os
import sys
import time

import cv2


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    label = sys.argv[2] if len(sys.argv) > 2 else "shot"
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1280
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 720

    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    frame = None
    for _ in range(20):                      # warm up: let exposure/white-balance settle
        ok, f = cap.read()
        if ok and f is not None and f.size > 0:
            frame = f
        time.sleep(0.06)
    cap.release()

    if frame is None:
        print(f"camera {idx}: no frame")
        return 1
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"{label}.png")
    cv2.imwrite(path, frame)
    print(f"camera {idx}: {frame.shape[1]}x{frame.shape[0]} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
