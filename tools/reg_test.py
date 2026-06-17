"""End-to-end test of registration + calibration from the START position (no empty)."""
import os
import sys

import cv2
import numpy as np
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard
from chessmon.board_state import grid_str, board_to_grid

OUT = os.path.join(ROOT, "out")
frame = cv2.imread(os.path.join(OUT, sys.argv[1] if len(sys.argv) > 1 else "reg.png"))

rb = RealBoard.from_start(frame)
t = rb.calibrate_orientation_auto(frame)
print(f"registered from pieces; orientation t={t} (a1 {'dark OK' if t is not None else 'NOT dark'})")
if t is None:
    sys.exit(1)
rb.learn(frame, chess.Board())
g = rb.classify(frame)
print(grid_str(g))
std = board_to_grid(chess.Board())
print(f"counts empty/light/dark: {(g==0).sum()}/{(g==1).sum()}/{(g==2).sum()}  "
      f"(want 32/16/16); matches standard start: {int((g==std).sum())}/64")
