"""Hardware-free tests for the real-camera layer: orientation solving (incl. 90deg)
and the delta-based CameraGame (robust to stable misreads). Grid-level - no images.

    python tests/test_camera.py
"""
import os
import sys

import numpy as np
import chess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chessmon.camera import (dihedral, solve_orientation, solve_orientation_by_color,
                             _square_is_light, decide_colour, CameraGame)
from chessmon.board_state import board_to_grid, square_to_rc, Cell

_FAIL = []


def check(cond, msg):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {msg}")
    if not cond:
        _FAIL.append(msg)


def after(*ucis):
    b = chess.Board()
    for u in ucis:
        b.push_uci(u)
    return board_to_grid(b)


def test_orientation_all_eight():
    print("orientation: solver recovers a working transform for every board rotation/flip")
    ts, ta = board_to_grid(chess.Board()), after("b2b4")
    for k in range(8):                       # the camera sees the board in orientation k
        cam_s, cam_a = dihedral(ts, k), dihedral(ta, k)
        t = solve_orientation(cam_s, cam_a, "b2b4")
        g = CameraGame()
        g.observe(dihedral(cam_s, t))
        kind, san, m = g.observe(dihedral(cam_a, t))
        label = ["0", "90", "180", "270", "0'", "90'", "180'", "270'"][k]
        check(kind == "move" and m is not None and m.uci() == "b2b4",
              f"camera at {label} -> reads b2b4 (got {kind} {san})")


def test_orientation_from_colours():
    print("orientation: square colours (a1 dark) fix orientation, no reference move")
    start = board_to_grid(chess.Board())
    chess_dark = np.array([[not _square_is_light(r, c) for c in range(8)] for r in range(8)])
    for k in range(4):                                  # the 4 physical board rotations
        cam_start, cam_dark = dihedral(start, k), dihedral(chess_dark, k)
        t = solve_orientation_by_color(cam_start, cam_dark)
        ok = (t is not None
              and np.array_equal(dihedral(cam_dark, t), chess_dark)     # a1 ends up dark
              and np.array_equal(dihedral(cam_start, t), start))        # white side correct
        check(ok, f"board rotated {k * 90} deg -> a1 dark + occupancy aligned (t={t})")


def test_ninety_degree_specifically():
    print("orientation: a 90deg board is handled")
    ts, ta = board_to_grid(chess.Board()), after("e2e4")
    cam_s, cam_a = dihedral(ts, 1), dihedral(ta, 1)     # 90 deg
    t = solve_orientation(cam_s, cam_a, "e2e4")
    g = CameraGame()
    g.observe(dihedral(cam_s, t))
    kind, san, m = g.observe(dihedral(cam_a, t))
    check(kind == "move" and m.uci() == "e2e4", f"90deg e2e4 -> {san}")


def test_stable_misread_cancels():
    print("CameraGame: a stable wrong-colour square does not break move detection")
    base = board_to_grid(chess.Board())
    err = base.copy(); err[0, 7] = 1                    # a black piece always reads light
    g = CameraGame()
    g.observe(err)                                      # baseline carries the error
    aft = after("b2b4").copy(); aft[0, 7] = 1           # error still present
    kind, san, m = g.observe(aft)
    check(kind == "move" and m.uci() == "b2b4", f"got {kind} {san}")


def test_sequence_with_capture():
    print("CameraGame: a short line incl. a capture reads correctly via deltas")
    b = chess.Board()
    g = CameraGame()
    g.observe(board_to_grid(b))
    sans = []
    for u in ["e2e4", "d7d5", "e4d5"]:
        b.push_uci(u)
        kind, san, m = g.observe(board_to_grid(b))
        check(kind == "move", f"{u}: {kind}")
        sans.append(san)
    check(sans == ["e4", "d5", "exd5"], f"SAN sequence {sans}")


def test_legality_resolves_unseen_dark_destination():
    print("legality: an unseen dark-on-dark destination is inferred (a7 vacated -> a5)")
    board = chess.Board()
    board.push_san("e4")                       # Black to move
    g = CameraGame(board.copy())
    g.observe(board_to_grid(board))            # baseline
    obs = board_to_grid(board).copy()
    obs[square_to_rc(chess.A7)] = Cell.EMPTY   # we see a7 vacate; a5 (dark) is missed
    kind, san, m = g.observe(obs)
    # a6 is a light square (a dark pawn there would be seen) -> ruled out; only a5 left
    check(kind == "move" and m is not None and m.uci() == "a7a5", f"got {kind} {san}")


def test_seen_contrasting_destination_is_used():
    print("legality: when the contrasting destination IS seen, it wins (a7 -> a6)")
    board = chess.Board()
    board.push_san("e4")
    g = CameraGame(board.copy())
    g.observe(board_to_grid(board))
    after = chess.Board(board.fen()); after.push_san("a6")
    kind, san, m = g.observe(board_to_grid(after))   # a6 occupied is visible
    check(kind == "move" and m.uci() == "a7a6", f"got {kind} {san}")


def test_per_square_colour_sample_beats_glare():
    print("colour: a per-square learned sample classifies a glared dark piece correctly")
    thr, g_light, g_dark = 50.0, 90.0, 15.0
    nan = float("nan")
    # a glared dark piece reads high (norm=70) -> the global threshold calls it 'light'
    check(decide_colour(70, nan, nan, g_light, g_dark, thr) == Cell.LIGHT,
          "threshold alone mis-reads the glared piece as light")
    # but with this square's learned dark sample (also high, 72), nearest-match -> dark
    check(decide_colour(70, nan, 72.0, g_light, g_dark, thr) == Cell.DARK,
          "per-square dark sample fixes it -> dark")
    # a genuine light piece on the same square still reads light
    check(decide_colour(95, nan, 72.0, g_light, g_dark, thr) == Cell.LIGHT,
          "a light piece there still reads light")


def test_unchanged_frame_is_nochange():
    print("CameraGame: an identical frame reports no change")
    g = CameraGame()
    g.observe(board_to_grid(chess.Board()))
    kind, _s, _m = g.observe(board_to_grid(chess.Board()))
    check(kind == "nochange", f"got {kind}")


def main():
    for t in [test_orientation_all_eight, test_orientation_from_colours,
              test_ninety_degree_specifically,
              test_stable_misread_cancels, test_sequence_with_capture,
              test_legality_resolves_unseen_dark_destination,
              test_seen_contrasting_destination_is_used,
              test_per_square_colour_sample_beats_glare,
              test_unchanged_frame_is_nochange]:
        t()
    print()
    if _FAIL:
        print(f"FAILED ({len(_FAIL)})")
        return 1
    print("ALL CAMERA TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
