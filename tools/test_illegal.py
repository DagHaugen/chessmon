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

# illegal: bishop c1 -> c3. c3 IS a legal landing (Nc3) so the arrival looks fine, but the blocked
# c1 bishop can't legally leave c1 -> the origin gives it away.
gb = CameraGame(chess.Board())
sb = board_to_grid(gb.board); gb.observe(sb)
ob = sb.copy()
ob[7][2] = Cell.EMPTY       # c1 vacates  (r = 8-1 = 7, c = 2)
ob[5][2] = Cell.LIGHT       # c3 occupied (r = 8-3 = 5, c = 2) by the bishop
kb, sanb, _ = gb.observe(ob)
assert kb == "error", f"illegal Bc1-c3 not flagged: {kb} ({sanb})"
print(f"illegal Bc1-c3 (legal landing c3, illegal origin c1) -> {kb} / {sanb}")

# illegal: rook h1 -> h4 as the FIRST move, h2 pawn still up. h4 is a legal pawn landing (h2-h4), but
# that pawn move is contradicted by h2 still occupied -> h4 is not a VIABLE arrival, so the stray white
# rook on dark h4 must be flagged (previously mis-handled as 'unseen' guesses).
gh = CameraGame(chess.Board())
sh = board_to_grid(gh.board); gh.observe(sh)
oh = sh.copy()
oh[7][7] = Cell.EMPTY        # h1 vacates  (r = 8-1 = 7, c = 7)
oh[4][7] = Cell.LIGHT        # h4 occupied (r = 8-4 = 4, c = 7) by the white rook (dark square -> high contrast)
kh, sanh, _ = gh.observe(oh)
assert kh == "error", f"illegal Rh1-h4 not flagged: {kh} ({sanh})"
print(f"illegal Rh1-h4 (h2 still up, h4 unreachable) -> {kh} / {sanh}")

# illegal: a high-contrast piece LEFT its square but its move is illegal and the landing isn't visible.
# White bishop on c1 (white on a DARK square -> its empty square is reliably seen). No legal move leaves
# c1 (blocked), so the clear vacate ALONE must flag it -- even though no arrival is shown.
gv = CameraGame(chess.Board())
sv = board_to_grid(gv.board); gv.observe(sv)
ov = sv.copy()
ov[7][2] = Cell.EMPTY        # c1 vacates (r = 8-1 = 7, c = 2); destination low-contrast / unseen
kv, sanv, _ = gv.observe(ov)
assert kv == "error", f"vacate-only illegal (c1 emptied, no landing) not flagged: {kv} ({sanv})"
print(f"illegal vacate-only (c1 emptied, landing unseen) -> {kv} / {sanv}")

# legal e2-e4 from the start must still register as a move (e4 IS a legal landing square)
g2 = CameraGame(chess.Board())
s2 = board_to_grid(g2.board); g2.observe(s2)
o2 = s2.copy(); o2[6][4] = Cell.EMPTY; o2[4][4] = Cell.LIGHT      # e2 -> e4 (r=4, c=4)
k2, san2, _ = g2.observe(o2)
assert k2 == "move" and san2 == "e4", f"legal e4 broke: {k2} {san2}"
print(f"legal e2-e4 still -> {k2} / {san2}")
print("ILLEGAL-ARRIVAL detection OK")
