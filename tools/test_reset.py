"""RESET: reset_game rebuilds the game to the start position; resnap re-anchors the baseline."""
import sys

import cv2

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from server.manager import SessionManager  # noqa: E402

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR"
mgr = SessionManager()
s = mgr.create_table()
frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_corners.png")
s.calibrate_oneshot(frame, [[120, 115], [840, 140], [870, 855], [100, 835]])
s.resolve_orientation("right")
s.resolve("e2e4")
assert len(s.moves) == 1 and s.game.board.fen().split()[0] != START, "setup move didn't take"

s.reset_game()
assert s.game.board.fen().split()[0] == START, "game not back to the start position"
assert not s.moves and s.result is None, "moves/result not cleared"

v = s.resnap(frame)                       # what the server does after reset (re-anchor the baseline)
assert v.get("type") == "refreshed", "re-baseline failed"
print("RESET: game back to start + baseline re-anchored  OK")
