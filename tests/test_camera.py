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


def test_castling_preferred_when_landing_unseen():
    print("castling: O-O with the king's landing unseen still reads O-O, not a 1-sq Kf8")
    board = chess.Board("rnbqk2r/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR b KQkq - 0 1")
    g = CameraGame(board.copy())
    prev = board_to_grid(board)
    prev[square_to_rc(chess.H8)] = Cell.EMPTY        # dark rook on dark h8 reads empty (real)
    g.observe(prev)                                  # baseline
    obs = prev.copy()
    obs[square_to_rc(chess.E8)] = Cell.EMPTY         # king seen to leave e8; g8/f8 landing unseen
    kind, san, m = g.observe(obs)
    check(kind == "move" and m is not None and m.uci() == "e8g8", f"got {kind} {san}")


def test_invisible_move_reports_unseen():
    print("evidence gate: an invisible move (no visible signal) is 'unseen', not a ghost")
    board = chess.Board("7k/8/8/8/8/8/8/3QK2R w K - 0 1")    # White Q d1, K e1, R h1
    g = CameraGame(board.copy())
    prev = board_to_grid(board)
    prev[square_to_rc(chess.D1)] = Cell.EMPTY        # the queen on light d1 reads empty
    g.observe(prev)                                  # baseline
    obs = prev.copy()
    obs[square_to_rc(chess.A7)] = Cell.LIGHT         # a stray flicker; no real move signal
    kind, san, extra = g.observe(obs)
    check(kind == "unseen", f"got {kind} {san} {extra}")


def test_dark_origin_reading_as_its_square_is_forgiven():
    print("origin: a knight leaving a dark square (origin still reads dark) commits via the arrival")
    board = chess.Board("4k3/8/5n2/8/8/8/8/4K3 b - - 0 1")   # black Nf6, Black to move
    g = CameraGame(board.copy())
    prev = board_to_grid(board)
    g.observe(prev)                                  # baseline (f6 reads dark)
    obs = prev.copy()
    obs[square_to_rc(chess.G4)] = Cell.DARK          # knight arrives g4 (dark on light = seen);
    kind, san, m = g.observe(obs)                    # f6 stays dark (empty dark square reads dark)
    check(kind == "move" and m is not None and m.uci() == "f6g4", f"got {kind} {san}")


def test_contrasting_origin_still_there_is_ruled_out():
    print("origin: a piece CONTRASTING its square that stays put is NOT a vacated origin")
    board = chess.Board("4k3/8/8/8/8/8/3P4/4K3 w - - 0 1")   # white pawn d2 (light pce, DARK sq)
    g = CameraGame(board.copy())
    prev = board_to_grid(board)
    g.observe(prev)
    obs = prev.copy()
    obs[square_to_rc(chess.D4)] = Cell.LIGHT         # d4 shows a piece, but d2 still reads light
    kind, san, m = g.observe(obs)                    # d2-d4 ruled out: the pawn clearly sits on d2
    check(kind != "move", f"d2-d4 should be ruled out (d2 still occupied); got {kind} {san}")


def test_visible_move_still_commits_not_unseen():
    print("evidence gate: a clearly visible move still commits (gate doesn't over-fire)")
    g = CameraGame()
    g.observe(board_to_grid(chess.Board()))
    kind, san, m = g.observe(after("e2e4"))          # e-pawn change is fully visible
    check(kind == "move" and m.uci() == "e2e4", f"got {kind} {san}")


def test_end_gesture_both_kings_to_centre():
    print("gesture: both kings to the centre (both on light) = White wins, not a legal move")
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")    # Ke1 + ke8, both on contrasting sqs
    g = CameraGame(board.copy())
    prev = board_to_grid(board)
    g.observe(prev)                                  # baseline
    obs = prev.copy()
    obs[square_to_rc(chess.E1)] = Cell.EMPTY         # white king leaves e1 (was visible)
    obs[square_to_rc(chess.E8)] = Cell.EMPTY         # black king leaves e8
    obs[square_to_rc(chess.D5)] = Cell.DARK          # black king now on light d5 (visible)
    kind, san, _ = g.observe(obs)                    # white king on light e4 stays invisible
    check(kind == "gesture" and san == "1-0", f"got {kind} {san}")


def test_single_king_move_is_not_gesture():
    print("gesture: a normal one-king move is NOT mistaken for the end-gesture")
    board = chess.Board("4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    g = CameraGame(board.copy())
    g.observe(board_to_grid(board))
    after = chess.Board(board.fen())
    after.push_san("Kd1")                            # only the white king moves
    kind, san, m = g.observe(board_to_grid(after))
    check(kind == "move" and m is not None and m.uci() == "e1d1", f"got {kind} {san}")


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


def test_obscured_board_is_unsettled():
    print("obscured: a hand-sized burst of change is 'unsettled', not a move or illegal")
    g = CameraGame()
    g.observe(board_to_grid(chess.Board()))            # baseline at the start
    kind, san, _ = g.observe(after("e2e4"))            # a real 2-square move passes the gate
    check(kind == "move" and san == "e4", f"a normal move is NOT tripped by the gate (got {kind} {san})")
    g = CameraGame()
    g.observe(board_to_grid(chess.Board()))
    hand = board_to_grid(chess.Board()).copy()
    for r, c in [(2, 0), (2, 1), (2, 2), (2, 3), (2, 4), (2, 5), (2, 6), (2, 7), (3, 0), (3, 1)]:
        hand[r, c] = Cell.LIGHT                         # 10 phantom occupied squares = a hand over the board
    kind, san, _ = g.observe(hand)
    check(kind == "unsettled", f"10 changed squares (a hand) -> unsettled, not error (got {kind})")


def main():
    for t in [test_orientation_all_eight, test_orientation_from_colours,
              test_ninety_degree_specifically,
              test_stable_misread_cancels, test_sequence_with_capture,
              test_legality_resolves_unseen_dark_destination,
              test_seen_contrasting_destination_is_used,
              test_castling_preferred_when_landing_unseen,
              test_invisible_move_reports_unseen,
              test_dark_origin_reading_as_its_square_is_forgiven,
              test_contrasting_origin_still_there_is_ruled_out,
              test_end_gesture_both_kings_to_centre,
              test_single_king_move_is_not_gesture,
              test_visible_move_still_commits_not_unseen,
              test_per_square_colour_sample_beats_glare,
              test_unchanged_frame_is_nochange,
              test_obscured_board_is_unsettled]:
        t()
    print()
    if _FAIL:
        print(f"FAILED ({len(_FAIL)})")
        return 1
    print("ALL CAMERA TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
