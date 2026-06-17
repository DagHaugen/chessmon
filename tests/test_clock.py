"""Headless verification of the clock device logic (no browser, injected clock).

Run:
    python tests/test_clock.py
"""
from __future__ import annotations

import os
import sys

import chess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chessmon.board_state import Cell, board_to_grid, square_to_rc
from chessmon.clock_server import ClockGame

_FAILURES: list[str] = []


def check(cond, msg):
    print(f"  [{'ok  ' if cond else 'FAIL'}] {msg}")
    if not cond:
        _FAILURES.append(msg)


def play(game, uci):
    """Simulate: make the physical move, then tap the clock to confirm."""
    game.make_move(uci)
    game.confirm()


def test_confirm_commits_and_switches():
    print("confirm: a tap commits the staged move and switches the clock")
    g = ClockGame()
    g.make_move("e2e4")
    check(g.staged and g.active == chess.WHITE, "after move: staged, still White to confirm")
    g.confirm()
    check(g.history == ["e4"], f"committed e4 (history={g.history})")
    check(g.active == chess.BLACK and not g.staged, "clock switched to Black")


def test_confirm_without_move_is_noop():
    print("confirm: tapping with nothing moved does not advance")
    g = ClockGame()
    g.confirm()
    check(g.history == [] and g.active == chess.WHITE, "no move committed")
    check("No move" in g.report, f"reported: {g.report}")


def test_capture_via_device():
    print("capture: device flow recovers a capture (destination flip)")
    g = ClockGame(start_fen="rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 1")
    play(g, "e4d5")
    check(g.history == ["exd5"], f"reported exd5 (history={g.history})")


def test_promotion_picker():
    print("promotion: CONFIRM defers to the device; underpromotion to Knight")
    # extra black pawn so King+Knight vs King isn't an instant insufficient-material draw
    g = ClockGame(start_fen="8/P6k/8/8/8/7p/8/K7 w - - 0 1")
    g.make_move("a7a8q")          # physical placeholder; type chosen on the device
    g.confirm()
    check(g.mode == "promotion", f"entered promotion mode (mode={g.mode})")
    check(g.active == chess.WHITE, "clock still on the promoting side")
    g.promote("n")               # tap the Knight button
    check(g.mode == "play", "back to play after choosing")
    check(g.history and g.history[-1] == "a8=N", f"recorded a8=N (history={g.history})")
    check(g.physical.piece_at(chess.A8).piece_type == chess.KNIGHT,
          "physical board now holds a Knight, not a Queen")
    check(g.active == chess.BLACK, "clock switched after promotion resolved")


def test_promotion_queen_default_button():
    print("promotion: the Queen button works and is the dominant choice")
    g = ClockGame(start_fen="8/P6k/8/8/8/7p/8/K7 w - - 0 1")
    g.make_move("a7a8q")
    g.confirm()
    snap = g.snapshot()
    check(snap["promo"] == ["q", "r", "b", "n"], "device offers Q,R,B,N (Queen first)")
    g.promote("q")
    check(g.history[-1] == "a8=Q", f"recorded a8=Q (history={g.history})")


def test_clock_counts_down_and_increments():
    print("clock: active side counts down; confirm adds the increment")
    g = ClockGame(base_seconds=100.0, increment=2.0)
    g.tick(1000.0)               # initialize
    g.tick(1003.0)               # 3s on White's clock
    check(abs(g.times[chess.WHITE] - 97.0) < 1e-6, f"White 97.0 (got {g.times[chess.WHITE]:.3f})")
    check(abs(g.times[chess.BLACK] - 100.0) < 1e-6, "Black untouched")
    g.make_move("e2e4")
    g.confirm()                  # +2 increment to White, switch to Black
    check(abs(g.times[chess.WHITE] - 99.0) < 1e-6, f"White 97+2=99 (got {g.times[chess.WHITE]:.3f})")
    g.tick(1005.0)               # 2s now run on Black
    check(abs(g.times[chess.BLACK] - 98.0) < 1e-6, f"Black 98.0 (got {g.times[chess.BLACK]:.3f})")


def test_flag_fall():
    print("clock: running a side to zero flags the game over")
    g = ClockGame(base_seconds=5.0, increment=0.0)
    g.tick(0.0)
    g.tick(6.0)                  # White overruns
    check(g.mode == "gameover" and g.winner == chess.BLACK, f"Black wins on time (winner={g.winner})")


def test_full_game_through_device():
    print("game: a short game played entirely through the device")
    g = ClockGame()
    for uci in ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6", "b5c6", "d7c6", "e1g1"]:
        play(g, uci)
    check(g.history == ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6", "O-O"],
          f"history correct ({' '.join(g.history)})")
    check(g.active == chess.BLACK, "Black to move after White castles")


def test_kings_to_center_encodes_result():
    print("gesture: kings on light squares=White, dark=Black, mixed=draw")
    for result, winner, score in [("white", chess.WHITE, "1-0"),
                                   ("black", chess.BLACK, "0-1"),
                                   ("draw", None, "1/2-1/2")]:
        g = ClockGame()
        play(g, "e2e4"); play(g, "e7e5")
        g.end_by_kings_to_center(result)
        check(g.mode == "gameover", f"{result}: mode gameover (got {g.mode})")
        check(g.winner == winner, f"{result}: winner {winner} (got {g.winner})")
        check(g.result == score, f"{result}: score {score} (got {g.result})")
        check("kings to the centre" in g.report, f"{result}: report mentions the gesture")


def test_decode_result_from_grid():
    print("gesture decode: result read straight from the kings' square colours")
    g = ClockGame()
    wk, bk = g.inf.board.king(chess.WHITE), g.inf.board.king(chess.BLACK)

    def grid_with(white_sq, black_sq):
        gr = board_to_grid(g.inf.board)
        gr[square_to_rc(wk)] = Cell.EMPTY
        gr[square_to_rc(bk)] = Cell.EMPTY
        gr[square_to_rc(white_sq)] = Cell.LIGHT  # white king = light piece
        gr[square_to_rc(black_sq)] = Cell.DARK   # black king = dark piece
        return gr

    check(g._decode_result(grid_with(chess.E4, chess.D5)) == chess.WHITE, "both on light -> White")
    check(g._decode_result(grid_with(chess.D4, chess.E5)) == chess.BLACK, "both on dark -> Black")
    check(g._decode_result(grid_with(chess.E4, chess.E5)) is None, "mixed colours -> draw")


def test_end_gesture_detection_and_safety():
    print("gesture: detected from occupancy; ordinary positions are not")
    g = ClockGame()
    board = g.inf.board
    check(not g._is_end_gesture(board_to_grid(board)), "start position is NOT the gesture")
    grid = board_to_grid(board)
    wk, bk = board.king(chess.WHITE), board.king(chess.BLACK)
    grid[square_to_rc(wk)] = Cell.EMPTY
    grid[square_to_rc(bk)] = Cell.EMPTY
    grid[square_to_rc(chess.E4)] = Cell.LIGHT
    grid[square_to_rc(chess.D5)] = Cell.DARK
    check(g._is_end_gesture(grid), "both kings in the centre IS the gesture")


def test_move_table_timing_and_positions():
    print("scoresheet: per-move elapsed times and position snapshots are recorded")
    g = ClockGame()
    g.tick(1000.0)                 # game start
    g.tick(1004.0)                 # 4s gone
    play(g, "e2e4")
    g.tick(1010.0)                 # 10s gone
    play(g, "e7e5")
    check(g.history == ["e4", "e5"], f"history {g.history}")
    check(g.start_wall == 1000.0, f"start_wall captured (got {g.start_wall})")
    check(g.move_elapsed == [4.0, 10.0], f"move times since start (got {g.move_elapsed})")
    check(len(g.positions) == 3, f"start + 2 positions (got {len(g.positions)})")
    check(g.positions[0] == chess.Board().board_fen(), "positions[0] = start position")
    after = chess.Board(); after.push_san("e4")
    check(g.positions[1] == after.board_fen(), "positions[1] = position after e4")


def test_chess960_setup_and_play():
    print("chess960: a 960 board flows through the device unchanged")
    g = ClockGame(chess960=True, position=420)
    check(g.physical.chess960, "physical board is in chess960 mode")
    check(g.variant == "chess960", "variant reported as chess960")
    check(g.position960 == 420, f"position number stored (got {g.position960})")
    play(g, "e2e4"); play(g, "e7e5")     # pawns sit on rank 2/7 in every 960 position
    check(g.history[:2] == ["e4", "e5"], f"pawns play normally (got {g.history})")


def test_chess960_castling_recovered():
    print("chess960: castling (king onto its rook) is recovered as O-O")
    g = ClockGame(start_fen="r3k2r/pppppppp/8/8/8/8/PPPPPPPP/R3K2R w KQkq - 0 1",
                  chess960=True)
    check("e1h1" in g.legal_ucis(), "kingside castle offered as e1h1 (king-to-rook)")
    play(g, "e1h1")
    check(g.history[-1] == "O-O", f"recovered and recorded as O-O (got {g.history})")


def test_single_king_move_not_gesture():
    print("gesture: one king moving (even toward the centre) does not trigger it")
    g = ClockGame(start_fen="4k3/8/8/8/8/8/P7/4K3 w - - 0 1")
    play(g, "e1e2")
    check(g.mode == "play", f"still playing after a lone king move (got {g.mode})")


def main():
    tests = [
        test_confirm_commits_and_switches,
        test_confirm_without_move_is_noop,
        test_capture_via_device,
        test_promotion_picker,
        test_promotion_queen_default_button,
        test_clock_counts_down_and_increments,
        test_flag_fall,
        test_full_game_through_device,
        test_kings_to_center_encodes_result,
        test_decode_result_from_grid,
        test_move_table_timing_and_positions,
        test_chess960_setup_and_play,
        test_chess960_castling_recovered,
        test_end_gesture_detection_and_safety,
        test_single_king_move_not_gesture,
    ]
    for t in tests:
        t()
    print()
    if _FAILURES:
        print(f"FAILED ({len(_FAILURES)}):")
        for m in _FAILURES:
            print("  -", m)
        return 1
    print("ALL CLOCK TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
