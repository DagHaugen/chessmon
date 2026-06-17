"""Real-board classification test (orientation-independent).

Registers out/empty.png (sector detector -> homography), warps out/start.png to
canonical, then for each of the 64 squares measures edge energy (occupancy) and
core luma (piece colour). Occupancy and colour thresholds are found with Otsu, so
this works regardless of how the board is rotated in the frame - it just answers
"does the real board separate cleanly into empty / light / dark?".

Writes out/start_warp.png and out/start_detected.png (overlay).
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
SQ, N = 80, 640


def edge_energy(roi):
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(np.sqrt(gx * gx + gy * gy)))


def luma(bgr):
    return float(0.114 * bgr[..., 0] + 0.587 * bgr[..., 1] + 0.299 * bgr[..., 2])


def roi(img, r, c, frac):
    m = int(SQ * (1 - frac) / 2)
    return img[r * SQ + m:(r + 1) * SQ - m, c * SQ + m:(c + 1) * SQ - m]


def register(path):
    img = cv2.imread(path)
    if img is None:
        return None, None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ok, c = cv2.findChessboardCornersSB(gray, (7, 7), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        return None, None
    src = c.reshape(-1, 2).astype(np.float32)
    dst = np.array([[(j + 1) * SQ, (i + 1) * SQ] for i in range(7) for j in range(7)],
                   dtype=np.float32)
    H, _ = cv2.findHomography(src, dst)
    return H, img


def otsu(vals):
    v = np.array(vals, np.float32)
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-6:
        return hi
    vn = cv2.normalize(v, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    t, _ = cv2.threshold(vn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return lo + (hi - lo) * t / 255.0


def main():
    H, empty = register(os.path.join(OUT, "empty.png"))
    if H is None:
        print("registration FAILED on empty.png")
        return 1
    start = cv2.imread(os.path.join(OUT, "start.png"))
    if start is None:
        print("no out/start.png yet")
        return 1
    we = cv2.warpPerspective(empty, H, (N, N))     # known-empty background reference
    ws = cv2.warpPerspective(start, H, (N, N))
    cv2.imwrite(os.path.join(OUT, "start_warp.png"), ws)

    edges = np.zeros((8, 8)); cdiff = np.zeros((8, 8)); lum = np.zeros((8, 8))
    for r in range(8):
        for c in range(8):
            cur = roi(ws, r, c, 0.45).astype(np.float32)        # middle of the square only
            bg = roi(we, r, c, 0.45).astype(np.float32)
            cdiff[r, c] = float(np.mean(np.abs(cur - bg)))      # PER-PIXEL change vs empty
            edges[r, c] = edge_energy(roi(ws, r, c, 0.45))      # texture (backup signal)
            lum[r, c] = luma(roi(ws, r, c, 0.30).reshape(-1, 3).mean(0))

    print("change-vs-empty per square:")
    print("\n".join(" ".join(f"{cdiff[r, c]:3.0f}" for c in range(8)) for r in range(8)))
    print("edge energy per square:")
    print("\n".join(" ".join(f"{edges[r, c]:3.0f}" for c in range(8)) for r in range(8)))

    # Absolute thresholds just above the empty-board floor (production will derive
    # these from a second empty frame); occupied if EITHER signal fires.
    occ = (cdiff > 22) | (edges > 10)
    ct = otsu(lum[occ]) if occ.any() else 128.0

    grid = np.zeros((8, 8), int)
    for r in range(8):
        for c in range(8):
            if occ[r, c]:
                grid[r, c] = 1 if lum[r, c] > ct else 2
    sym = {0: ".", 1: "O", 2: "X"}
    print(f"colour_thresh={ct:.1f}  (occupancy = edge OR change-vs-empty)")
    print("\n".join(" ".join(sym[grid[r, c]] for c in range(8)) for r in range(8)))
    print(f"counts  empty={int((grid==0).sum())}  light={int((grid==1).sum())}  dark={int((grid==2).sum())}")

    vis = ws.copy()
    for r in range(8):
        for c in range(8):
            if grid[r, c]:
                cx, cy = int((c + 0.5) * SQ), int((r + 0.5) * SQ)
                col = (245, 245, 245) if grid[r, c] == 1 else (30, 30, 30)
                cv2.circle(vis, (cx, cy), 12, col, -1)
                cv2.circle(vis, (cx, cy), 12, (0, 170, 0), 2)
    cv2.imwrite(os.path.join(OUT, "start_detected.png"), vis)
    print("wrote start_warp.png and start_detected.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
