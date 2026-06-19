"""Confirm a calibrated session (RealBoard + python-chess game + clocks) pickles and restores."""
import pickle
import sys

import cv2

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from server.manager import SessionManager  # noqa: E402

mgr = SessionManager()
s = mgr.create_table("Alice", "Bob")
tok, pair = s.table_token, s.pair_token
frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_corners.png")
s.calibrate_oneshot(frame, [[120, 115], [840, 140], [870, 855], [100, 835]])
s.resolve_orientation("right")
s.resolve("e2e4")
print("before:  calibrated", s.board_reader is not None, "| moves", len(s.moves),
      "| pieces", s.game.board.fen().split()[0][:14])

data = pickle.dumps(mgr._by_table)               # raises here if anything is unpicklable
print("pickled", len(data) // 1024, "KB")
restored = pickle.loads(data)
s2 = restored[tok]
assert s2.board_reader is not None and len(s2.moves) == 1 and s2.pair_token == pair, "lost state"
assert s2._calib_frame is None, "transient frame should not persist"
print("after:   calibrated", s2.board_reader is not None, "| moves", len(s2.moves),
      "| pieces", s2.game.board.fen().split()[0][:14], " PERSIST ROUNDTRIP OK")
