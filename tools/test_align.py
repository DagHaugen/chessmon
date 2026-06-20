"""Offline test for camera-movement detection (Session.check_alignment).

Takes a real calibrated board frame, captures the corner reference patches, then feeds back:
  * the same frame                       -> no movement (alignment fine)
  * the frame with one interior square changed (a piece move) -> no movement (corners untouched)
  * the frame shifted a few %             -> movement detected + the console alert raised
  * the original frame again              -> self-heals (alert clears)
No camera or server needed. Run: .venv\\Scripts\\python tools\\test_align.py
"""
import os
import sys

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.game_session import Session

FAILED = 0


def check(cond, label):
    global FAILED
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}")
    if not cond:
        FAILED += 1


def fresh(frame, corners):
    s = Session("t-align")
    s.camera_dev = "cam1"
    s.corners = corners
    s.align_refs = {"cam1": s._capture_align_refs(frame)}
    return s


FRAME = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "start.png")
frame = cv2.imread(FRAME)
if frame is None:
    print(f"need a sample frame at {FRAME}")
    sys.exit(2)

h, w = frame.shape[:2]
corners = [[0.15, 0.12], [0.85, 0.12], [0.85, 0.88], [0.15, 0.88]]
print(f"camera-movement detection on {os.path.basename(FRAME)} ({w}x{h}), 4 corner patches")

# 1) STABLE: the same frame, twice -> never flags
s = fresh(frame, corners)
v1 = s.check_alignment(frame)
v2 = s.check_alignment(frame)
check(v1 is None and v2 is None and not s.alignment_alert, "a stable board never flags movement")

# 2) PIECE MOVE: change one interior square's worth of pixels -> corners unchanged -> no flag
moved_piece = frame.copy()
cy, cx = int(0.45 * h), int(0.55 * w)                 # a central square, well inside the board
d = max(20, w // 24)
moved_piece[cy - d:cy + d, cx - d:cx + d] = 0         # blot out a square (a captured/placed piece)
s = fresh(frame, corners)
s.check_alignment(moved_piece)
v = s.check_alignment(moved_piece)
check(v is None and not s.alignment_alert, "a local change (a piece move) does NOT read as movement")

# 3) CAMERA MOVED: shift the whole frame a couple % (> the move threshold, < the search radius)
dx, dy = int(0.02 * w), int(0.015 * w)
shifted = np.roll(np.roll(frame, dy, axis=0), dx, axis=1)
s = fresh(frame, corners)
first = s.check_alignment(shifted)                    # strike 1: holds the move...
check(first is not None and first.get("type") == "move.unclear", "a shifted frame HOLDS the move (move.unclear)")
check(not s.alignment_alert, "  ...but the console alert waits for confirmation (debounce)")
second = s.check_alignment(shifted)                   # strike 2: confirmed -> console alert
check(second is not None and s.alignment_alert, "a second shifted frame raises the 'camera moved' alert")
check("re-calibrate" in second.get("reason", ""), "the verdict tells the operator to re-calibrate")

# 4) SELF-HEAL: the camera/board goes back -> the alert clears on the next clean frame
back = s.check_alignment(frame)
check(back is None and not s.alignment_alert, "a clean frame self-heals the alert")

print("ALL ALIGN TESTS OK" if not FAILED else f"{FAILED} CHECK(S) FAILED")
sys.exit(1 if FAILED else 0)
