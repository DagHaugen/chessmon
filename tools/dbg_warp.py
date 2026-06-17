"""Save the rectified empty + current frame with the sampled centre boxes drawn,
so we can see what actually contaminates the middle squares."""
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, OCC_ROI

OUT = os.path.join(ROOT, "out")
SQ = 80
m = int(SQ * (1 - OCC_ROI) / 2)
rb = RealBoard(cv2.imread(os.path.join(OUT, "empty.png")))
for name in ("empty", "live_frame"):
    w = rb.warp(cv2.imread(os.path.join(OUT, f"{name}.png")))
    for r in range(8):
        for c in range(8):
            cv2.rectangle(w, (c * SQ + m, r * SQ + m),
                          ((c + 1) * SQ - m, (r + 1) * SQ - m), (0, 255, 0), 1)
    cv2.imwrite(os.path.join(OUT, f"{name}_roi.png"), w)
print("saved empty_roi.png and live_frame_roi.png")
