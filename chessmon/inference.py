"""Move inference: a stream of three-state grids -> chess moves.

The state machine keeps a believed `chess.Board`. For each settled observation
it does NOT pattern-match by hand; instead it asks python-chess for every legal
move, projects the resulting position down to a three-state grid, and keeps the
move(s) whose projection equals the observation. Legality is therefore the
safety net: almost every vision error maps to zero legal moves and is rejected
rather than silently corrupting the game.

This single mechanism covers every move type uniformly:
  * quiet move / capture .... the capture's flip (enemy colour -> mover colour)
                              is reproduced by the projected position
  * castling ................ king + rook relocation reproduced
  * en passant .............. the captured pawn's empty square reproduced
  * promotion ............... all of {Q,R,B,N} project to the same grid, so they
                              tie -> we report it and assume Queen
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import chess

from .board_state import Cell, board_to_grid, grids_equal


@dataclass
class Result:
    kind: str                       # move | incomplete | nochange | ambiguous | error
    san: str | None = None
    move: chess.Move | None = None
    note: str | None = None
    candidates: list | None = None


def _only_removals(committed: np.ndarray, grid: np.ndarray) -> bool:
    """True if `grid` is `committed` with one or more pieces simply lifted off
    (squares went occupied->empty) and nothing added or colour-flipped.

    No legal move ever looks like this (every move *places* a piece), so such a
    frame is a mid-move transient - a piece in hand, or a capture's victim
    removed before the capturer lands. The caller waits for the next settle.
    """
    changed = committed != grid
    if not changed.any():
        return False
    for r, c in zip(*np.where(changed)):
        if not (committed[r, c] != Cell.EMPTY and grid[r, c] == Cell.EMPTY):
            return False
    return True


class MoveInference:
    def __init__(self, board: chess.Board | None = None, allow_two_ply: bool = True,
                 interactive_promotion: bool = False):
        self.board = board.copy() if board is not None else chess.Board()
        self.committed = board_to_grid(self.board)
        self.allow_two_ply = allow_two_ply
        # When True, an ambiguous promotion is reported (kind="promotion") and held
        # for the device to resolve via resolve_promotion(), instead of assuming a
        # Queen. This is the one thing colour-only vision genuinely cannot observe.
        self.interactive_promotion = interactive_promotion
        self.pending_promotion: tuple[int, int, list[chess.Move]] | None = None

    # -- core matcher: which legal move(s) reproduce this grid -----------------
    def _match_one(self, grid: np.ndarray) -> list[chess.Move]:
        out = []
        for m in self.board.legal_moves:
            self.board.push(m)
            ok = grids_equal(board_to_grid(self.board), grid)
            self.board.pop()
            if ok:
                out.append(m)
        return out

    def _match_two(self, grid: np.ndarray) -> list[tuple[chess.Move, chess.Move]]:
        """Recover a missed ply: find a unique (our move, their move) pair whose
        final position matches. Used only when single-move matching fails."""
        out = []
        for m1 in list(self.board.legal_moves):
            self.board.push(m1)
            for m2 in self.board.legal_moves:
                self.board.push(m2)
                ok = grids_equal(board_to_grid(self.board), grid)
                self.board.pop()
                if ok:
                    out.append((m1, m2))
            self.board.pop()
        return out

    # -- the observation entry point ------------------------------------------
    def observe(self, grid: np.ndarray) -> Result:
        if grids_equal(grid, self.committed):
            return Result("nochange")
        if _only_removals(self.committed, grid):
            return Result("incomplete", note="piece lifted / mid-move; waiting")

        cands = self._match_one(grid)
        if len(cands) == 1:
            return self._commit(cands[0])
        if len(cands) > 1:
            squares = {(m.from_square, m.to_square) for m in cands}
            if len(squares) == 1:  # underpromotion ambiguity only
                if self.interactive_promotion:
                    frm, to = cands[0].from_square, cands[0].to_square
                    self.pending_promotion = (frm, to, cands)
                    return Result("promotion", candidates=cands,
                                  note="choose promotion piece on the device")
                q = next((m for m in cands if m.promotion == chess.QUEEN), cands[0])
                return self._commit(q, note="promotion type unobservable; assumed Queen")
            return Result("ambiguous", candidates=cands,
                          note="several legal moves share this occupancy")

        if self.allow_two_ply:
            pairs = self._match_two(grid)
            if len(pairs) == 1:
                m1, m2 = pairs[0]
                s1 = self._commit(m1).san
                s2 = self._commit(m2).san
                return Result("move", san=f"{s1} {s2}", move=m2,
                              note="recovered a missed ply (two half-moves)")

        return Result("error", note="no legal move reproduces this occupancy")

    def resolve_promotion(self, piece_type: int) -> Result:
        """Finalize a held promotion with the type the device reported."""
        if self.pending_promotion is None:
            return Result("error", note="no promotion is pending")
        _frm, _to, cands = self.pending_promotion
        move = next((m for m in cands if m.promotion == piece_type), None)
        if move is None:
            return Result("error", note="invalid promotion piece")
        self.pending_promotion = None
        return self._commit(move, note=f"promoted to {chess.piece_name(piece_type)}")

    def _commit(self, move: chess.Move, note: str | None = None) -> Result:
        san = self.board.san(move)       # must be computed before pushing
        self.board.push(move)
        self.committed = board_to_grid(self.board)
        return Result("move", san=san, move=move, note=note)
