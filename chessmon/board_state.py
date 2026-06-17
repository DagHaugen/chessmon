"""Three-state board representation and the grid <-> python-chess mapping.

A "grid" is an 8x8 uint8 numpy array whose cells are `Cell` values. This is the
only data structure that crosses the vision -> inference boundary.

Orientation convention (one place, used everywhere):
    grid[row][col]
    row 0  = rank 8 (top of the image)      row 7 = rank 1 (white's back rank)
    col 0  = file a (left)                  col 7 = file h
So white sits at the bottom of the frame, which is how a camera above the board
with White nearest the operator would see it.
"""
from __future__ import annotations

from enum import IntEnum

import numpy as np
import chess


class Cell(IntEnum):
    EMPTY = 0
    LIGHT = 1  # a white piece occupies the square
    DARK = 2   # a black piece occupies the square


GRID_SHAPE = (8, 8)


def empty_grid() -> np.ndarray:
    return np.zeros(GRID_SHAPE, dtype=np.uint8)


def square_to_rc(square: int) -> tuple[int, int]:
    """python-chess square index (0 = a1) -> (row, col) in our convention."""
    file = chess.square_file(square)
    rank = chess.square_rank(square)
    return (7 - rank, file)


def rc_to_square(r: int, c: int) -> int:
    return chess.square(c, 7 - r)


def board_to_grid(board: chess.Board) -> np.ndarray:
    """Project a full chess position down to its three-state occupancy grid.

    This deliberate loss of information (piece type is discarded, only colour
    survives) is exactly what the camera can observe. Inference works by
    comparing observed grids against board_to_grid(candidate position).
    """
    g = empty_grid()
    for sq in chess.SQUARES:
        p = board.piece_at(sq)
        if p is not None:
            r, c = square_to_rc(sq)
            g[r, c] = Cell.LIGHT if p.color == chess.WHITE else Cell.DARK
    return g


def grids_equal(a: np.ndarray, b: np.ndarray) -> bool:
    return np.array_equal(a, b)


def grid_str(g: np.ndarray) -> str:
    """Pretty 8x8 dump: '.' empty, 'O' white piece, 'X' black piece."""
    sym = {0: ".", 1: "O", 2: "X"}
    return "\n".join(" ".join(sym[int(v)] for v in row) for row in g)
