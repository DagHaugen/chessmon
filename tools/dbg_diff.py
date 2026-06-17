"""Show the baseline grid, the current live_frame grid, and what changed."""
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard
from chessmon.board_state import grid_str

OUT = os.path.join(ROOT, "out")
s = json.load(open(os.path.join(OUT, "live.json")))
rb = RealBoard(cv2.imread(os.path.join(OUT, "empty.png")))
rb.t, rb.color_thr = s["t"], s["color_thr"]
prev = np.array(s["prev"], dtype=int)
cur = rb.classify(cv2.imread(os.path.join(OUT, "live_frame.png")))
print("baseline:\n" + grid_str(prev))
print("current:\n" + grid_str(cur))
changed = np.argwhere(prev != cur)
print(f"changed squares: {len(changed)}")
lab = {0: "empty", 1: "light", 2: "dark"}
for r, c in changed:
    print(f"  ({r},{c}): {lab[int(prev[r, c])]} -> {lab[int(cur[r, c])]}")
