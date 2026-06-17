"""Track board changes: classify a frame and report what changed vs the last one.

    python tools/track.py out/start.png baseline   # set the reference position
    python tools/track.py out/move1.png            # diff vs previous -> the move

Registration + per-pixel background come from out/empty.png. Each square is read
from its MIDDLE only (robust ROI). Occupied if it changed vs the empty board OR
has edge texture. State (last grid + colour threshold) persists in
out/track_state.npz so consecutive calls report vacated / appeared / flipped.
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")
SQ, N, FRAC = 80, 640, 0.45
LABEL = {0: "empty", 1: "light", 2: "dark"}


def roi(img, r, c, frac):
    m = int(SQ * (1 - frac) / 2)
    return img[r * SQ + m:(r + 1) * SQ - m, c * SQ + m:(c + 1) * SQ - m]


def edge_energy(x):
    g = cv2.cvtColor(x, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(np.sqrt(gx * gx + gy * gy)))


def luma(b):
    return float(0.114 * b[..., 0] + 0.587 * b[..., 1] + 0.299 * b[..., 2])


def register(path):
    img = cv2.imread(path)
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ok, c = cv2.findChessboardCornersSB(g, (7, 7), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        return None, None
    src = c.reshape(-1, 2).astype(np.float32)
    dst = np.array([[(j + 1) * SQ, (i + 1) * SQ] for i in range(7) for j in range(7)], np.float32)
    H, _ = cv2.findHomography(src, dst)
    return H, img


def otsu(vals):
    v = np.asarray(vals, np.float32)
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-6:
        return hi
    vn = cv2.normalize(v, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    t, _ = cv2.threshold(vn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return lo + (hi - lo) * t / 255.0


def classify(frame_path, ct=None):
    H, empty = register(os.path.join(OUT, "empty.png"))
    if H is None:
        raise SystemExit("registration failed on out/empty.png")
    img = cv2.imread(frame_path)
    we = cv2.warpPerspective(empty, H, (N, N))
    ws = cv2.warpPerspective(img, H, (N, N))
    cdiff = np.zeros((8, 8)); edges = np.zeros((8, 8)); lum = np.zeros((8, 8))
    for r in range(8):
        for c in range(8):
            cur = roi(ws, r, c, FRAC).astype(np.float32)
            bg = roi(we, r, c, FRAC).astype(np.float32)
            cdiff[r, c] = float(np.mean(np.abs(cur - bg)))
            edges[r, c] = edge_energy(roi(ws, r, c, FRAC))
            lum[r, c] = luma(roi(ws, r, c, 0.30).reshape(-1, 3).mean(0))
    occ = (cdiff > 22) | (edges > 10)
    if ct is None:
        ct = otsu(lum[occ]) if occ.any() else 128.0
    grid = np.zeros((8, 8), int)
    grid[occ] = np.where(lum[occ] > ct, 1, 2)
    return grid, ct


def show(grid):
    s = {0: ".", 1: "O", 2: "X"}
    return "\n".join(" ".join(s[grid[r, c]] for c in range(8)) for r in range(8))


def name(r, c):       # this rig: white on rows 0/1 (ranks 1/2), files mirrored
    return "abcdefgh"[7 - c] + str(r + 1)     # file = 7-col, rank = row+1 (180deg)


def main():
    if len(sys.argv) < 2:
        print("usage: track.py <image> [baseline]")
        return 1
    rel = sys.argv[1]
    path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    baseline = len(sys.argv) > 2 and sys.argv[2] == "baseline"
    state = os.path.join(OUT, "track_state.npz")

    prev, ct = None, None
    if os.path.exists(state) and not baseline:
        d = np.load(state)
        prev, ct = d["grid"], float(d["ct"])

    grid, ct = classify(path, ct)
    print(show(grid))
    if prev is None:
        print("-> baseline set")
    else:
        changed = np.argwhere(prev != grid)
        if len(changed) == 0:
            print("-> no change")
        else:
            for r, c in changed:
                print(f"  {name(r, c)}: {LABEL[int(prev[r, c])]} -> {LABEL[int(grid[r, c])]}")
            vacated = [(r, c) for r, c in changed if grid[r, c] == 0]
            arrived = [(r, c) for r, c in changed if prev[r, c] == 0 and grid[r, c] != 0]
            flipped = [(r, c) for r, c in changed if prev[r, c] != 0 and grid[r, c] != 0]
            if len(vacated) == 1 and len(arrived) == 1 and not flipped:
                print(f"-> move {name(*vacated[0])}-{name(*arrived[0])}")
            elif len(vacated) == 1 and len(flipped) == 1 and not arrived:
                print(f"-> capture {name(*vacated[0])}x{name(*flipped[0])}")
    np.savez(state, grid=grid, ct=ct)
    return 0


if __name__ == "__main__":
    sys.exit(main())
