"""Try to register a board image: find the 7x7 inner corners, rectify to canonical.

    python tools/register.py [image]   (default out/empty.png)

Tries the sector-based detector (robust to perspective/lighting) then the classic
one. On success writes out/<name>_corners.png (overlay) and out/<name>_warp.png
(the rectified 640x640 board).
"""
import os
import sys

import cv2
import numpy as np

SQ = 80
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "out")


def find_corners(gray):
    # Sector-based (OpenCV >= 4.x): handles perspective + uneven light better.
    try:
        found, c = cv2.findChessboardCornersSB(gray, (7, 7), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
        if found:
            return found, c, "SB"
    except Exception as e:
        print("  SB error:", e)
    flags = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
    found, c = cv2.findChessboardCorners(gray, (7, 7), flags=flags)
    return found, c, "classic"


def main():
    rel = sys.argv[1] if len(sys.argv) > 1 else "out/empty.png"
    path = rel if os.path.isabs(rel) else os.path.join(ROOT, rel)
    img = cv2.imread(path)
    if img is None:
        print("could not read", path)
        return 1
    base = os.path.splitext(os.path.basename(path))[0]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    found, corners, how = find_corners(gray)
    print(f"{base}: {img.shape[1]}x{img.shape[0]}  inner-corner grid found={found} ({how})")
    if not found:
        # save a contrast-stretched gray to help eyeball why
        cv2.imwrite(os.path.join(OUT, f"{base}_gray.png"),
                    cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX))
        print("  -> FAILED. wrote", f"{base}_gray.png")
        return 2
    vis = img.copy()
    cv2.drawChessboardCorners(vis, (7, 7), corners, found)
    cv2.imwrite(os.path.join(OUT, f"{base}_corners.png"), vis)
    src = corners.reshape(-1, 2).astype(np.float32)
    dst = np.array([[(c + 1) * SQ, (r + 1) * SQ] for r in range(7) for c in range(7)],
                   dtype=np.float32)
    H, _ = cv2.findHomography(src, dst)
    warp = cv2.warpPerspective(img, H, (8 * SQ, 8 * SQ))
    cv2.imwrite(os.path.join(OUT, f"{base}_warp.png"), warp)
    print(f"  -> OK ({how}). wrote {base}_corners.png and {base}_warp.png")
    return 0


if __name__ == "__main__":
    sys.exit(main())
