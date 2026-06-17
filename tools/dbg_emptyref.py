"""Classify the current frame against the ORIGINAL empty.png references instead of the
evolving live_ref.png - to check whether live_ref.png got corrupted (a piece pasted in
as 'empty', which makes that square read empty forever after)."""
import os
import sys

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import live
from chessmon.camera import RealBoard
from chessmon.board_state import grid_str

s = live.load()
rb = RealBoard(cv2.imread(os.path.join(live.OUT, "empty.png")))   # original empty references
rb.t = s["t"]
rb.color_thr = s["color_thr"]
rb.global_light = s.get("global_light")
rb.global_dark = s.get("global_dark")
frame = cv2.imread(os.path.join(live.OUT, "live_frame.png"))
print("classified vs ORIGINAL empty.png (ranks 4-5 = centre):")
print(grid_str(rb.classify(frame)))
