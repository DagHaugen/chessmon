"""Server session-logic tests — drive the pure `Session` directly with synthetic grids,
so the move loop / resolve / gesture / PGN are covered with no camera and no FastAPI.

    python tests/test_server.py
"""
import os
import sys

import chess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.game_session import Session
from chessmon.board_state import board_to_grid, square_to_rc, Cell

_FAIL = []


def check(cond, msg):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {msg}")
    if not cond:
        _FAIL.append(msg)


def grid_after(*ucis):
    b = chess.Board()
    for u in ucis:
        b.push_uci(u)
    return board_to_grid(b)


def test_move_loop_records_pgn_and_clocks():
    print("session: a short line is detected, recorded with clocks, and exported as PGN")
    s = Session("tok", white="Ada", black="Bob")
    s.seed_baseline(board_to_grid(chess.Board()))
    s.confirm("white", 300, 300)
    v1 = s.ingest_grid(grid_after("e2e4"))
    check(v1["type"] == "move.result" and v1["san"] == "e4", f"1. e4 -> {v1}")
    s.confirm("black", 300, 294)
    v2 = s.ingest_grid(grid_after("e2e4", "e7e5"))
    check(v2["san"] == "e5" and v2["clock_black"] == 294, f"1... e5 -> {v2}")
    snap = s.snapshot()
    check(len(snap["moves"]) == 2, f"two moves recorded ({len(snap['moves'])})")
    check("1. e4 e5" in snap["pgn"], f"PGN has the line: {snap['pgn']!r}")
    check(snap["turn"] == "White", f"White to move ({snap['turn']})")


def test_resolve_commits_player_choice():
    print("session: resolve() commits the move the player tapped after an unclear read")
    s = Session("tok")
    s.seed_baseline(board_to_grid(chess.Board()))
    s._last_grid = grid_after("d2d4")
    v = s.resolve("d2d4")
    check(v["type"] == "move.result" and v["san"] == "d4", f"resolve d2d4 -> {v}")
    check(s.snapshot()["fen"].split()[0].endswith("3P4/8/PPP1PPPP/RNBQKBNR"), "board advanced")


def test_kings_to_centre_ends_game():
    print("session: both kings to the centre ends the game with the decoded result")
    s = Session("tok", start_fen="4k3/8/8/8/8/8/8/4K3 w - - 0 1")
    base = board_to_grid(s.game.board)
    s.seed_baseline(base)
    obs = base.copy()
    obs[square_to_rc(chess.E1)] = Cell.EMPTY        # white king leaves e1
    obs[square_to_rc(chess.E8)] = Cell.EMPTY        # black king leaves e8
    obs[square_to_rc(chess.D5)] = Cell.DARK         # black king now on light d5 (visible)
    v = s.ingest_grid(obs)
    check(v["type"] == "game.end" and v["result"] == "1-0", f"gesture -> {v}")
    check(s.result == "1-0", "result stored on the session")


def main():
    for t in [test_move_loop_records_pgn_and_clocks,
              test_resolve_commits_player_choice,
              test_kings_to_centre_ends_game]:
        t()
    print()
    if _FAIL:
        print(f"FAILED ({len(_FAIL)})")
        return 1
    print("ALL SERVER TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
