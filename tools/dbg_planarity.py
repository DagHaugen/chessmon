"""Is the board flat? Fit a single homography to the 7x7 inner corners and show the
reprojection residual per corner. A flat board -> small, uniform residuals. A
folding board creased at the middle -> a ridge of large residuals along the fold.
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
SQ = 80

img = cv2.imread(os.path.join(OUT, "empty.png"))
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
ok, c = cv2.findChessboardCornersSB(gray, (7, 7), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
if not ok:
    print("no corners")
    sys.exit(1)
src = c.reshape(-1, 2).astype(np.float32)
dst = np.array([[(j + 1) * SQ, (i + 1) * SQ] for i in range(7) for j in range(7)], np.float32)
H, _ = cv2.findHomography(src, dst)
proj = cv2.perspectiveTransform(src.reshape(-1, 1, 2), H).reshape(-1, 2)
resid = np.linalg.norm(proj - dst, axis=1).reshape(7, 7)

print("homography residual per inner corner (px); top row = far side, fold is the middle row:")
for r in range(7):
    print(" ".join(f"{resid[r, c]:4.1f}" for c in range(7)))
print(f"mean={resid.mean():.2f}px  max={resid.max():.2f}px")
print("per-row mean:", " ".join(f"{resid[r].mean():.1f}" for r in range(7)))
