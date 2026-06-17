"""Webcam-only: rectify a real frame into the canonical top-down board.

Strategy (no extra dependencies beyond opencv-python): detect the 7x7 grid of
inner corners on an EMPTY board with `findChessboardCorners`, then solve a
homography onto the canonical pixel grid. Re-run whenever the board is bumped.

Caveats to resolve at integration time (documented, not yet handled here):
  * findChessboardCorners has a rotation/reflection ambiguity. Disambiguate
    using the known start position (which side actually holds the white pieces)
    and flip the homography if needed.
  * ArUco markers at the four corners would be more robust than inner-corner
    detection and would survive a partially occupied board; swap this in if the
    plain detector proves flaky on the real table.
"""
from __future__ import annotations

import numpy as np
import cv2


def find_board_homography(empty_frame, square_px: int = 80):
    """Return a 3x3 homography mapping the raw frame to the canonical board, or
    None if the inner-corner grid could not be found."""
    gray = cv2.cvtColor(empty_frame, cv2.COLOR_BGR2GRAY)
    found, corners = cv2.findChessboardCorners(
        gray, (7, 7),
        flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
    )
    if not found:
        return None
    src = corners.reshape(-1, 2).astype(np.float32)
    dst = np.array(
        [[(c + 1) * square_px, (r + 1) * square_px] for r in range(7) for c in range(7)],
        dtype=np.float32,
    )
    H, _ = cv2.findHomography(src, dst)
    return H


def warp(frame, H, square_px: int = 80):
    n = 8 * square_px
    return cv2.warpPerspective(frame, H, (n, n))
