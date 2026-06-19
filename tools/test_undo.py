"""UNDO pops the last move (no-op when empty); _delta_squares maps a change-mask to square names."""
import sys

import numpy as np

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from server.manager import SessionManager  # noqa: E402

s = SessionManager().create_table()
s.resolve("e2e4")
s.resolve("e7e5")
assert len(s.moves) == 2
assert s.undo_move() and len(s.moves) == 1
assert s.game.board.fen().split()[0] == "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR", "not back to after 1.e4"
assert s.undo_move() and len(s.moves) == 0 and not s.game.board.move_stack
assert s.undo_move() is False, "undo should be a no-op with nothing to take back"

d = np.zeros((8, 8), bool); d[6, 4] = True; d[4, 4] = True   # board_to_grid coords for e2 + e4
assert set(s._delta_squares(d)) == {"e2", "e4"}, s._delta_squares(d)
assert s._delta_squares(None) == []
print("UNDO (pop to start, no-op when empty) + delta->squares (e2,e4)  OK")
