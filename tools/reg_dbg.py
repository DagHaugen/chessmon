import os
import sys

import cv2
import numpy as np
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, dihedral, _square_is_light
from chessmon.board_state import grid_str, board_to_grid

OUT = os.path.join(ROOT, "out")
frame = cv2.imread(os.path.join(OUT, "reg.png"))
rb = RealBoard.from_start(frame)
rb.calibrate_color(frame)
gs = rb._grid(*rb._measure(frame), rb.color_thr)
print("start grid (camera orientation):")
print(grid_str(gs))
print("dark_sq (#=dark):")
print("\n".join("".join("#" if rb.dark_sq[r, c] else "." for c in range(8)) for r in range(8)))
target = board_to_grid(chess.Board())
chess_dark = np.array([[not _square_is_light(r, c) for c in range(8)] for r in range(8)])
for t in range(8):
    print(f"t={t}: occ {int((dihedral(gs, t) == target).sum())}/64  "
          f"dark_aligned={np.array_equal(dihedral(rb.dark_sq, t), chess_dark)}")
