"""Registration dev step 3: homography from the central (7,5) band -> rectify the
whole board (marker-free, no empty frame). Then refine on all 49 predicted corners."""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
SQ, N = 80, 640
img = cv2.imread(os.path.join(OUT, sys.argv[1] if len(sys.argv) > 1 else "reg.png"))
gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

# 1. anchor: largest central (7,k) band
size = next(((7, r) for r in (5, 4, 3)
             if cv2.findChessboardCornersSB(gray, (7, r), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)[0]), None)
ok, corners = cv2.findChessboardCornersSB(gray, size, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
src = corners.reshape(-1, 2).astype(np.float32)
rows = size[1]
row_off = (7 - rows) // 2                          # central band -> centre rows of the 7x7
# canonical target for inner corner (R,C) is ((C+1)*SQ,(R+1)*SQ); local k=i*7+j
dst = np.array([[((k % 7) + 1) * SQ, ((k // 7) + row_off + 1) * SQ] for k in range(len(src))],
               dtype=np.float32)
H, _ = cv2.findHomography(src, dst)

# 2. predict ALL 49 inner corners, refine each on the real image, keep good ones
grid = np.array([[(c + 1) * SQ, (r + 1) * SQ] for r in range(7) for c in range(7)], np.float32)
Hinv = np.linalg.inv(H)
pred = cv2.perspectiveTransform(grid.reshape(-1, 1, 2), Hinv).reshape(-1, 1, 2).astype(np.float32)
refined = cv2.cornerSubPix(gray, pred.copy(), (9, 9), (-1, -1),
                           (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01))
moved = np.linalg.norm(refined.reshape(-1, 2) - pred.reshape(-1, 2), axis=1)
keep = moved < 6.0                                 # corners that locked onto a real saddle
print(f"anchor {size}; refined corners kept: {int(keep.sum())}/49")
H2, _ = cv2.findHomography(refined.reshape(-1, 2)[keep], grid[keep])

for tag, Hm in (("rough", H), ("refined", H2)):
    cv2.imwrite(os.path.join(OUT, f"reg_warp_{tag}.png"), cv2.warpPerspective(img, Hm, (N, N)))
print("wrote reg_warp_rough.png and reg_warp_refined.png")
