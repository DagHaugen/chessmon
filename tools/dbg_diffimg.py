"""Amplified |current - empty| in canonical space, with grid + centre boxes drawn,
to SEE what differs inside each square (shadows? seam? leaning pieces?)."""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, OCC_ROI

OUT = os.path.join(ROOT, "out")
SQ = 80
m = int(SQ * (1 - OCC_ROI) / 2)
rb = RealBoard(cv2.imread(os.path.join(OUT, "empty.png")))
we = rb.warp(cv2.imread(os.path.join(OUT, "empty.png")))
wl = rb.warp(cv2.imread(os.path.join(OUT, "live_frame.png")))
diff = cv2.absdiff(wl, we)
amp = np.clip(diff.astype(np.float32) * 2.5, 0, 255).astype(np.uint8)
for r in range(9):
    cv2.line(amp, (0, r * SQ), (8 * SQ, r * SQ), (40, 40, 40), 1)
    cv2.line(amp, (r * SQ, 0), (r * SQ, 8 * SQ), (40, 40, 40), 1)
for r in range(8):
    for c in range(8):
        cv2.rectangle(amp, (c * SQ + m, r * SQ + m),
                      ((c + 1) * SQ - m, (r + 1) * SQ - m), (0, 255, 0), 1)
cv2.imwrite(os.path.join(OUT, "diff_amp.png"), amp)
print("saved diff_amp.png (bright = changed vs empty board)")
