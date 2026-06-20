"""Session test: an illegal move flags just its from/to (not the whole noisy change-mask) and reports a
display FEN with the piece where it physically sits. No camera / server.
Run: .venv\\Scripts\\python tools\\test_illegal_display.py
"""
import os
import sys

import numpy as np
import chess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.game_session import Session
from chessmon.board_state import Cell, board_to_grid

FAIL = 0


def check(cond, label):
    global FAIL
    print(f"  [{'ok  ' if cond else 'FAIL'}] {label}")
    if not cond:
        FAIL += 1


def rc(sq):                                   # 'e2' -> (row, col) with row 0 = rank 8
    return (8 - int(sq[1]), ord(sq[0]) - 97)


s = Session("t-ill")
start = board_to_grid(chess.Board())
s.seed_baseline(start)                        # baseline = the start position

# illegal: the e2 pawn jumps to e5 (vacate e2, land e5) + a low-contrast GHOST on g4 (noise to ignore)
obs = start.copy()
obs[rc("e2")] = Cell.EMPTY                     # piece clearly left e2
obs[rc("e5")] = Cell.LIGHT                     # white pawn now on e5 (a dark square -> high contrast, really seen)
obs[rc("g4")] = Cell.LIGHT                     # a light "piece" on a light square -> low contrast ghost, must be dropped

v = s.ingest_grid(obs)
print("illegal e2->e5 (+ ghost on g4):", v.get("type"), v.get("squares"))
check(v["type"] == "move.unclear", "flagged as a warning")
check(set(v.get("squares", [])) == {"e2", "e5"}, "flags exactly the from/to (e2,e5) -- ghost dropped, not 5 squares")

# the display FEN shows the pawn where it physically is (e5), origin empty
b = chess.Board()
b.set_board_fen(v["fen"])
check(b.piece_at(chess.E5) is not None and b.piece_at(chess.E5).piece_type == chess.PAWN,
      "display board: the pawn sits on e5 (where it is)")
check(b.piece_at(chess.E2) is None, "display board: e2 is empty (not where it was)")
check(np.array_equal(s._last_grid, start), "illegal reverts the snapshot to the last valid move (stray frame dropped)")

# the user's exact case: rook h1 -> h4 as the FIRST move (h2 pawn still up). Previously fell through to
# 'unseen' guesses; now flagged with just the from/to and the rook shown where it sits.
s2 = Session("t-ill2")
s2.seed_baseline(board_to_grid(chess.Board()))
o2 = board_to_grid(chess.Board()).copy()
o2[rc("h1")] = Cell.EMPTY
o2[rc("h4")] = Cell.LIGHT                      # white rook on dark h4 (high contrast, clearly seen)
v2 = s2.ingest_grid(o2)
print("rook h1->h4 (first move, h2 still up):", v2.get("type"), v2.get("squares"))
check(v2["type"] == "move.unclear" and set(v2.get("squares", [])) == {"h1", "h4"},
      "Rh1-h4 flagged illegal with just from/to (h1,h4), not wild guesses")
b2 = chess.Board()
b2.set_board_fen(v2["fen"])
check(b2.piece_at(chess.H4) is not None and b2.piece_at(chess.H4).piece_type == chess.ROOK
      and b2.piece_at(chess.H1) is None, "display board: the rook sits on h4 (where it is)")

# Bc1-c3 (illegal) with a STRAY high-contrast piece on h4 (noise / a piece left from an earlier test).
# Must flag the bishop's c1->c3, NOT the far-off h4.
s3 = Session("t-ill3")
s3.seed_baseline(board_to_grid(chess.Board()))
o3 = board_to_grid(chess.Board()).copy()
o3[rc("c1")] = Cell.EMPTY                      # bishop leaves c1
o3[rc("c3")] = Cell.LIGHT                      # white bishop on c3 (dark square -> high contrast)
o3[rc("h4")] = Cell.LIGHT                      # a stray high-contrast piece on h4 (dark square) -> must be ignored
v3 = s3.ingest_grid(o3)
print("Bc1-c3 (+ stray on h4):", v3.get("type"), v3.get("squares"))
check(v3["type"] == "move.unclear" and set(v3.get("squares", [])) == {"c1", "c3"},
      "Bc1-c3 flags c1,c3 (the bishop), NOT the far stray on h4")

# Ng1-g3 (illegal): g1 is a legal knight origin AND g3 a legal pawn landing, but NO single move does
# both. Both squares are high contrast -> must flag g1,g3 (not guess).
s4 = Session("t-ill4")
s4.seed_baseline(board_to_grid(chess.Board()))
o4 = board_to_grid(chess.Board()).copy()
o4[rc("g1")] = Cell.EMPTY
o4[rc("g3")] = Cell.LIGHT
v4 = s4.ingest_grid(o4)
print("Ng1-g3 (g1 origin, g3 landing, no single move):", v4.get("type"), v4.get("squares"))
check(v4["type"] == "move.unclear" and set(v4.get("squares", [])) == {"g1", "g3"},
      "Ng1-g3 flagged with g1,g3 (no single legal move does both)")
b4 = chess.Board()
b4.set_board_fen(v4["fen"])
check(b4.piece_at(chess.G3) is not None and b4.piece_at(chess.G3).piece_type == chess.KNIGHT
      and b4.piece_at(chess.G1) is None, "display board: the knight sits on g3")

# the user's actual board: Ng1-g3 with a SHADOW reading as a (dark) piece on h3 (which made "Nh3" a
# guess). g3 (the knight, light) must still win over the h3 shadow, and it's still illegal.
s5 = Session("t-ill5")
s5.seed_baseline(board_to_grid(chess.Board()))
o5 = board_to_grid(chess.Board()).copy()
o5[rc("g1")] = Cell.EMPTY
o5[rc("g3")] = Cell.LIGHT                      # the knight (light) on dark g3
o5[rc("h3")] = Cell.DARK                       # a shadow reading as a DARK piece on light h3
v5 = s5.ingest_grid(o5)
print("Ng1-g3 (+ shadow on h3):", v5.get("type"), v5.get("squares"))
check(v5["type"] == "move.unclear" and set(v5.get("squares", [])) == {"g1", "g3"},
      "Ng1-g3 with an h3 shadow still flags g1,g3 (g3 wins over the shadow)")

print("ALL ILLEGAL-DISPLAY TESTS OK" if not FAIL else f"{FAIL} CHECK(S) FAILED")
sys.exit(1 if FAIL else 0)
