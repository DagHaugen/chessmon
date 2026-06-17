"""Hardware-free verification of the whole vision -> inference loop.

Run directly (no pytest needed):
    python tests/test_suite.py
Exits non-zero if any check fails.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import chess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chessmon import synth
from chessmon.board_state import Cell, board_to_grid, empty_grid
from chessmon.detector import Calibration, StabilityGate
from chessmon.inference import MoveInference
from chessmon.app import build_calibration, GAME, SQUARE_PX, RENDER_KW

_FAILURES: list[str] = []


def check(cond: bool, msg: str) -> None:
    mark = "ok  " if cond else "FAIL"
    print(f"  [{mark}] {msg}")
    if not cond:
        _FAILURES.append(msg)


def test_game_recovers_every_move():
    print("game: full Ruy Lopez Exchange (captures + castling)")
    calib = build_calibration()
    truth = chess.Board()
    inf = MoveInference()
    for i, san in enumerate(GAME):
        move = truth.parse_san(san)
        truth.push(move)
        frame = synth.render(board_to_grid(truth), SQUARE_PX, seed=100 + i, **RENDER_KW)
        grid, _ = calib.classify(frame)
        res = inf.observe(grid)
        check(res.move == move, f"ply {i + 1} {san}: reported {res.san}")


def test_capture_seen_at_destination():
    print("capture: destination flip is observed (the headline three-state win)")
    calib = build_calibration()
    board = chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    inf = MoveInference(board)
    board.push_san("exd5")  # pawn captures on an already-occupied square
    frame = synth.render(board_to_grid(board), SQUARE_PX, seed=7, **RENDER_KW)
    grid, _ = calib.classify(frame)
    res = inf.observe(grid)
    check(res.kind == "move" and res.san == "exd5", f"reported {res.san} ({res.kind})")


def test_en_passant():
    print("en passant: three squares change, recovered correctly")
    calib = build_calibration()
    fen = "rnbqkbnr/ppppp1pp/8/4Pp2/8/8/PPPP1PPP/RNBQKBNR w KQkq f6 0 3"
    board = chess.Board(fen)
    inf = MoveInference(board)
    board.push_san("exf6")
    frame = synth.render(board_to_grid(board), SQUARE_PX, seed=8, **RENDER_KW)
    grid, _ = calib.classify(frame)
    res = inf.observe(grid)
    check(res.kind == "move" and res.san == "exf6", f"reported {res.san} ({res.kind})")


def test_promotion_assumes_queen():
    print("promotion: piece type unobservable, defaults to Queen with a note")
    calib = build_calibration()
    board = chess.Board("8/P6k/8/8/8/8/8/K7 w - - 0 1")
    inf = MoveInference(board)
    promo = board_to_grid(chess.Board("Q7/7k/8/8/8/8/8/K7 b - - 0 1"))
    frame = synth.render(promo, SQUARE_PX, seed=9, **RENDER_KW)
    grid, _ = calib.classify(frame)
    res = inf.observe(grid)
    check(res.kind == "move" and res.move is not None
          and res.move.promotion == chess.QUEEN, f"reported {res.san} ({res.kind})")
    check(res.note is not None and "Queen" in res.note, f"note: {res.note}")


def test_incomplete_when_piece_lifted():
    print("transient: a lifted piece is 'incomplete', never committed")
    inf = MoveInference()
    grid = board_to_grid(chess.Board())
    grid[6, 4] = Cell.EMPTY  # white e2 pawn lifted (row6,col4)
    res = inf.observe(grid)
    check(res.kind == "incomplete", f"reported {res.kind}")
    check(np.array_equal(inf.committed, board_to_grid(chess.Board())),
          "committed board unchanged")


def test_missed_ply_recovered():
    print("missed ply: two half-moves between settles are decomposed")
    inf = MoveInference()
    board = chess.Board()
    board.push_san("e4")
    board.push_san("e5")
    grid = board_to_grid(board)
    res = inf.observe(grid)
    check(res.kind == "move" and res.san == "e4 e5", f"reported {res.san} ({res.kind})")


def test_illegal_observation_flagged():
    print("garbage: an impossible grid is flagged, not committed")
    inf = MoveInference()
    grid = board_to_grid(chess.Board())
    grid[4, 4] = Cell.DARK  # a black piece teleports to e4 - no legal white move
    res = inf.observe(grid)
    check(res.kind == "error", f"reported {res.kind}")


def test_shadow_and_low_contrast_classifier():
    print("classifier: shadows rejected, same-colour pieces still detected")
    calib = build_calibration()
    g = empty_grid()
    g[0, 0] = Cell.LIGHT  # white piece on a light square (r+c even) - hard case
    g[0, 1] = Cell.DARK   # black piece on a dark square (r+c odd)  - hard case
    frame = synth.render(g, SQUARE_PX, seed=11, **RENDER_KW)
    grid, _ = calib.classify(frame)
    check(grid[0, 0] == Cell.LIGHT, "white-on-light detected as LIGHT")
    check(grid[0, 1] == Cell.DARK, "black-on-dark detected as DARK")
    # every other square (empty, but carrying soft shadows) must read EMPTY
    occupied = int(np.count_nonzero(grid))
    check(occupied == 2, f"exactly 2 occupied squares (shadows rejected); got {occupied}")


def test_stability_gate_fires_once():
    print("stability gate: fires once per settle, after motion")
    gate = StabilityGate(settle_frames=3, motion_thresh=8.0)
    still = np.full((80, 80), 100, np.uint8)
    moving = np.full((80, 80), 200, np.uint8)
    fires = []
    # settle (3 still frames) -> 1 fire; motion; settle again -> 1 more fire
    seq = [still, still, still, still, moving, moving, still, still, still, still]
    for f in seq:
        fires.append(gate.update(f))
    check(sum(fires) == 2, f"fired {sum(fires)} times (expected 2)")


def main() -> int:
    tests = [
        test_game_recovers_every_move,
        test_capture_seen_at_destination,
        test_en_passant,
        test_promotion_assumes_queen,
        test_incomplete_when_piece_lifted,
        test_missed_ply_recovered,
        test_illegal_observation_flagged,
        test_shadow_and_low_contrast_classifier,
        test_stability_gate_fires_once,
    ]
    for t in tests:
        t()
    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}):")
        for m in _FAILURES:
            print("  -", m)
        return 1
    print("ALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
