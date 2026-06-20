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

# illegal: knight g1 -> g3. g1 IS a legal knight origin (Nf3/Nh3/Ne2) and g3 IS a legal pawn landing
# (g2-g3), so each square ALONE is explainable -- but NO single legal move does g1->g3. Both squares are
# high contrast (knight on a dark square), so the high-contrast pair must be flagged, not guessed.
gg = CameraGame(chess.Board())
sg = board_to_grid(gg.board); gg.observe(sg)
og = sg.copy()
og[7][6] = Cell.EMPTY        # g1 vacates  (r = 8-1 = 7, c = 6) -- white knight on dark g1
og[5][6] = Cell.LIGHT        # g3 occupied (r = 8-3 = 5, c = 6) -- the knight, on dark g3
kg, sang, _ = gg.observe(og)
assert kg == "error", f"illegal Ng1-g3 not flagged: {kg} ({sang})"
print(f"illegal Ng1-g3 (g1 a knight origin, g3 a pawn landing, no single move) -> {kg} / {sang}")

# illegal: black bishop c8 -> f5 (blocked by the d7 pawn). c8 and f5 are LIGHT squares holding a DARK
# (black) piece -> high contrast, so the c8 vacate + the landing fit no single move -> illegal, NOT a
# guessed g7-g6. Tall-piece note: even if the bishop's TOP reads one square over on g6 (parallax) and f5
# reads empty, the SEEN c8 vacate still makes it illegal.
bc = chess.Board(); bc.push_san("e4")          # black to move; bishop on c8, pawn on g7
for label, (vac, land) in {"f5 (clean)": ((0, 2), (3, 5)), "g6 (parallax)": ((0, 2), (2, 6))}.items():
    gc = CameraGame(bc.copy()); gc.observe(board_to_grid(bc))
    oc = board_to_grid(bc).copy()
    oc[vac[0]][vac[1]] = Cell.EMPTY            # c8 vacates
    oc[land[0]][land[1]] = Cell.DARK           # the bishop lands (f5, or its top on g6)
    kc, sanc, _ = gc.observe(oc)
    assert kc == "error", f"illegal Bc8-f5 [{label}] not flagged: {kc} ({sanc})"
    print(f"illegal Bc8-f5 [{label}] -> {kc} / {sanc}")

# REGRESSION: a PERSISTENT high-contrast mis-read (the camera always sees a dark blob on empty e6 --
# a leaning piece / standing shadow / bad calib) must NOT make legal moves illegal. Only CHANGES count,
# and a persistent blob sits in both the baseline and now, so it never enters the high-contrast set.
gp = CameraGame(chess.Board())
base = board_to_grid(chess.Board()); base[2][4] = Cell.DARK     # a ghost on e6 (r=2,c=4), present AT baseline
gp.observe(base)                                                # baseline includes the ghost
op = base.copy(); op[6][4] = Cell.EMPTY; op[4][4] = Cell.LIGHT  # e2-e4 (legal), ghost still on e6
kp, sanp, _ = gp.observe(op)
assert kp == "move" and sanp == "e4", f"legal e2-e4 with a persistent ghost must be a move, got {kp} {sanp}"
print(f"legal e2-e4 despite a persistent e6 ghost -> {kp} / {sanp}")

# legal e2-e4 from the start must still register as a move (e4 IS a legal landing square)
g2 = CameraGame(chess.Board())
s2 = board_to_grid(g2.board); g2.observe(s2)
o2 = s2.copy(); o2[6][4] = Cell.EMPTY; o2[4][4] = Cell.LIGHT      # e2 -> e4 (r=4, c=4)
k2, san2, _ = g2.observe(o2)
assert k2 == "move" and san2 == "e4", f"legal e4 broke: {k2} {san2}"
print(f"legal e2-e4 still -> {k2} / {san2}")
print("ILLEGAL-ARRIVAL detection OK")
