"""Correct a mis-detected last move: rebuild the believed game with the right move,
re-learn colour samples + reference from the actual frame, re-baseline.

    python tools/fixmove.py <uci>
"""
import json
import os
import sys

import cv2
import numpy as np
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard
from chessmon.board_state import grid_str

OUT = os.path.join(ROOT, "out")
STATE = os.path.join(OUT, "live.json")
REF = os.path.join(OUT, "live_ref.png")
GAME = ["Nc3", "d5", "b3", "Nf6", "Nxd5"]        # the correctly-tracked moves so far

board = chess.Board()
for m in GAME:
    board.push_san(m)
board.push_uci(sys.argv[1])                      # the real last move

s = json.load(open(STATE))
rb = RealBoard.from_start(cv2.imread(os.path.join(OUT, "live_start.png")))
if os.path.exists(REF):
    rb.we = cv2.imread(REF)
rb.t, rb.color_thr = s["t"], s["color_thr"]
rb.global_light, rb.global_dark = s.get("global_light"), s.get("global_dark")
frame = cv2.imread(os.path.join(OUT, "live_frame.png"))
rb.learn(frame, board)
rb.update_bg(frame, board)


def r2l(a):
    return [[None if np.isnan(v) else round(float(v), 2) for v in row] for row in a]


s["fen"] = board.fen()
s["ref_light"], s["ref_dark"] = r2l(rb.ref_light), r2l(rb.ref_dark)
s["global_light"], s["global_dark"] = rb.global_light, rb.global_dark
s["prev"] = rb.classify(frame).tolist()
json.dump(s, open(STATE, "w"))
cv2.imwrite(REF, rb.we)
print(grid_str(rb.classify(frame)))
print(f"corrected last move to {sys.argv[1]}; now {'White' if board.turn else 'Black'} to move")
