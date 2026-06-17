"""Diagnose a shot: show classified-now vs baseline vs believed, and the delta."""
import os
import sys

import cv2
import numpy as np
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import live
from chessmon.board_state import grid_str, board_to_grid

s = live.load()
rb = live.board_reader()
frame = cv2.imread(os.path.join(live.OUT, "live_frame.png"))
g = rb.classify(frame)
prev = np.array(s["prev"], dtype=np.uint8)
believed = board_to_grid(chess.Board(s["fen"]))
print("believed (model):"); print(grid_str(believed))
print("baseline prev:"); print(grid_str(prev))
print("classified now:"); print(grid_str(g))
print(f"changed (now vs prev): {int((g != prev).sum())} squares")
print(f"now vs believed mismatch: {int((g != believed).sum())} squares")
