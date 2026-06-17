"""Check the physical square-colour detection + the a1-is-dark rule on the real board."""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, dihedral, _square_is_light

OUT = os.path.join(ROOT, "out")
rb = RealBoard(cv2.imread(os.path.join(OUT, "empty.png")))
chess_dark = np.array([[not _square_is_light(r, c) for c in range(8)] for r in range(8)])

print("detected physical dark squares (camera orientation; #=dark/red, .=cream):")
print("\n".join(" ".join("#" if rb.dark_sq[r, c] else "." for c in range(8)) for r in range(8)))
good = [t for t in range(8) if np.array_equal(dihedral(rb.dark_sq, t), chess_dark)]
print("orientations whose colours satisfy 'a1 dark':", good)
print("under our rig's t=2, a1 corner is",
      "DARK (ok)" if dihedral(rb.dark_sq, 2)[7, 0] else "LIGHT (wrong!)")
