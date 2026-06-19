"""Load-time prune: an empty (never-calibrated, no-game) table is dropped; a calibrated one survives."""
import os
import sys

import cv2

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from server.manager import SessionManager  # noqa: E402

mgr = SessionManager()
empty = mgr.create_table()                              # paired but never used (the "stuck MQMcCZ" case)
full = mgr.create_table()
frame = cv2.imread(r"C:\Claude\Projects\chessmon\out\cam_corners.png")
full.calibrate_oneshot(frame, [[120, 115], [840, 140], [870, 855], [100, 835]])
full.resolve_orientation("right")
full.resolve("e2e4")
path = r"C:\Claude\Projects\chessmon\out\test_prune.pkl"
mgr.save(path)
print("saved 2 tables (1 empty, 1 calibrated+move)")

mgr2 = SessionManager()
mgr2.load(path)                                          # what a restart does — prunes on load
assert mgr2.by_table(full.table_token) is not None, "calibrated table was wrongly pruned"
assert mgr2.by_table(empty.table_token) is None, "empty table was NOT pruned"
assert mgr2.by_pair(full.pair_token) is not None, "pair index lost the kept table"
assert mgr2.by_pair(empty.pair_token) is None, "pair index kept the pruned table"
print(f"after load: {len(mgr2._by_table)} table -> empty pruned, calibrated kept  PRUNE OK")
os.remove(path)
