"""Persistent single-frame grab: try several backend/format combos for one index.

    python tools/cam_grab_robust.py [index] [label]

Logitech cameras on Windows often refuse to stream via the default MSMF path
(MF error -1072875772) until you force an MJPG fourcc and an explicit
resolution. This walks a list of combos and saves the first frame it gets.
"""
import os
import sys
import time

import cv2

MJPG = cv2.VideoWriter_fourcc(*"MJPG")


def attempt(idx, backend, bname, mjpg, w, h):
    cap = cv2.VideoCapture(idx, backend)
    if not cap.isOpened():
        cap.release()
        return None, f"{bname:5} mjpg={int(mjpg)} {w}x{h}: not opened"
    if mjpg:
        cap.set(cv2.CAP_PROP_FOURCC, MJPG)
    if w:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    frame = None
    for _ in range(25):
        cap.grab()
        ok, f = cap.retrieve()
        if ok and f is not None and f.size > 0:
            frame = f
            break
        time.sleep(0.08)
    gw, gh = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return frame, f"{bname:5} mjpg={int(mjpg)} req={w}x{h} got={gw}x{gh} frame={'YES' if frame is not None else 'no'}"


def main():
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    label = sys.argv[2] if len(sys.argv) > 2 else f"cam{idx}"
    combos = [
        (cv2.CAP_MSMF, "MSMF", True, 1280, 720),
        (cv2.CAP_MSMF, "MSMF", True, 640, 480),
        (cv2.CAP_DSHOW, "DSHOW", True, 1280, 720),
        (cv2.CAP_DSHOW, "DSHOW", False, 0, 0),
        (cv2.CAP_MSMF, "MSMF", False, 0, 0),
    ]
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    os.makedirs(out, exist_ok=True)
    print(f"probing index {idx}")
    for backend, bname, mjpg, w, h in combos:
        frame, desc = attempt(idx, backend, bname, mjpg, w, h)
        print("  " + desc)
        if frame is not None:
            path = os.path.join(out, f"{label}.png")
            cv2.imwrite(path, frame)
            print(f"OK: index {idx} -> {path}")
            return 0
    print(f"FAIL: index {idx} produced no frame")
    return 1


if __name__ == "__main__":
    sys.exit(main())
