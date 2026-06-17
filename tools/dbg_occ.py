"""Print per-square change-vs-empty / edge for the last live frame, to tune thresholds."""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, _roi, _edge, OCC_ROI

OUT = os.path.join(ROOT, "out")
rb = RealBoard(cv2.imread(os.path.join(OUT, "empty.png")))
w = rb.warp(cv2.imread(os.path.join(OUT, sys.argv[1] if len(sys.argv) > 1 else "live_frame.png")))
print(f"edge_thr={rb.edge_thr:.1f}  cdiff_thr={rb.cdiff_thr:.1f}   (cells show cdiff/edge)")
for r in range(8):
    cells = []
    for c in range(8):
        roi = _roi(w, r, c, OCC_ROI).astype(np.float32)
        cd = float(np.mean(np.abs(roi - rb.bg[r, c])))
        ed = _edge(_roi(w, r, c, OCC_ROI))
        cells.append(f"{cd:3.0f}/{ed:2.0f}")
    print(" ".join(cells))
