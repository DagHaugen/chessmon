"""Illegal moves are flagged: a clearly-visible piece on a square no legal move can reach -> error."""
import sys

import chess

sys.path.insert(0, r"C:\Claude\Projects\chessmon")
from chessmon.camera import CameraGame          # noqa: E402
from chessmon.board_state import board_to_grid, Cell  # noqa: E402

# baseline = start position
g = CameraGame(chess.Board())
start = board_to_grid(g.board)
g.observe(start)

# illegal: a white piece jumps e2 -> e5 (e5 is dark => white piece is high-contrast; no legal move reaches e5)
obs = start.copy()
obs[6][4] = Cell.EMPTY      # e2 vacates  (r = 8-2 = 6, c = 4)
obs[3][4] = Cell.LIGHT      # e5 occupied (r = 8-5 = 3, c = 4) -- a white piece, illegal
kind, san, _ = g.observe(obs)
assert kind == "error", f"illegal e2->e5 not flagged: {kind} ({san})"
print(f"illegal e2->e5 (white on dark, unreachable) -> {kind} / {san}")

# legal e2-e4 from the start must still register as a move (e4 IS a legal landing square)
g2 = CameraGame(chess.Board())
s2 = board_to_grid(g2.board); g2.observe(s2)
o2 = s2.copy(); o2[6][4] = Cell.EMPTY; o2[4][4] = Cell.LIGHT      # e2 -> e4 (r=4, c=4)
k2, san2, _ = g2.observe(o2)
assert k2 == "move" and san2 == "e4", f"legal e4 broke: {k2} {san2}"
print(f"legal e2-e4 still -> {k2} / {san2}")
print("ILLEGAL-ARRIVAL detection OK")
