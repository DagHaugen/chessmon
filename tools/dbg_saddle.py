"""Detect inner corners as SADDLE points (X-junctions) on a piece-laden board, to
show they're individually visible even when off-the-shelf full-grid detection fails.
A checkerboard corner is a saddle: det(Hessian) < 0. Piece edges are step edges, not
saddles, so they're largely suppressed.
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
name = sys.argv[1] if len(sys.argv) > 1 else "start.png"
img = cv2.imread(os.path.join(OUT, name))
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

g = cv2.GaussianBlur(gray, (0, 0), 2.0)
Ixx = cv2.Sobel(g, cv2.CV_32F, 2, 0, ksize=5)
Iyy = cv2.Sobel(g, cv2.CV_32F, 0, 2, ksize=5)
Ixy = cv2.Sobel(g, cv2.CV_32F, 1, 1, ksize=5)
saddle = np.maximum(0.0, -(Ixx * Iyy - Ixy * Ixy))     # >0 only where the Hessian is a saddle
saddle = cv2.GaussianBlur(saddle, (0, 0), 1.0)

mx = cv2.dilate(saddle, np.ones((21, 21), np.float32))  # non-max suppression
peaks = (saddle == mx) & (saddle > 0.06 * saddle.max())
ys, xs = np.where(peaks)
vis = img.copy()
for x, y in zip(xs, ys):
    cv2.circle(vis, (int(x), int(y)), 6, (0, 0, 255), 2)
cv2.imwrite(os.path.join(OUT, "saddle.png"), vis)
print(f"{len(xs)} saddle (inner-corner) peaks detected on {name}")
