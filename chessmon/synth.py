"""Synthetic top-down chessboard renderer for hardware-free testing.

Pieces are drawn as filled discs with a contrasting ring (the ring guarantees
edge energy even for the hard "white piece on a light square" case, mimicking
the silhouette/shading a real piece shows under a top-down camera).

Realism knobs let the test suite stress the detector:
  noise     - gaussian sensor noise (fraction of 255)
  lighting  - smooth multiplicative gradient across the board (+/- lighting/2)
  shadow    - soft, blurred drop shadows (low edge energy -> must NOT be read
              as occupancy; this is the shadow-rejection test)
  jitter    - random per-piece off-centring in pixels (pieces not placed dead
              centre)
All randomness is seeded so renders are reproducible.
"""
from __future__ import annotations

import numpy as np
import cv2

from .board_state import Cell

# BGR colours (OpenCV order)
LIGHT_SQ = (208, 236, 235)    # cream
DARK_SQ = (86, 149, 119)      # green
WHITE_PIECE = (250, 250, 250)
BLACK_PIECE = (40, 40, 40)
WHITE_RING = (90, 90, 90)
BLACK_RING = (205, 205, 205)


def render(grid: np.ndarray, square_px: int = 80, *, noise: float = 0.0,
           lighting: float = 0.0, shadow: bool = False, jitter: int = 0,
           seed: int = 0) -> np.ndarray:
    """Render a three-state grid to a BGR image that imitates a camera frame."""
    rng = np.random.RandomState(seed)
    n = 8 * square_px
    img = np.zeros((n, n, 3), dtype=np.uint8)

    # board squares
    for r in range(8):
        for c in range(8):
            color = LIGHT_SQ if (r + c) % 2 == 0 else DARK_SQ
            y0, x0 = r * square_px, c * square_px
            img[y0:y0 + square_px, x0:x0 + square_px] = color

    # soft drop shadows (drawn before pieces, heavily blurred -> low edge energy)
    if shadow:
        mask = np.zeros((n, n), np.float32)
        off = square_px // 6
        for r in range(8):
            for c in range(8):
                if grid[r, c] != Cell.EMPTY:
                    cy = int((r + 0.5) * square_px) + off
                    cx = int((c + 0.5) * square_px) + off
                    cv2.ellipse(mask, (cx, cy), (square_px // 3, square_px // 4),
                                0, 0, 360, 1.0, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), square_px * 0.25)
        img = (img.astype(np.float32) * (1.0 - 0.30 * mask[..., None]))
        img = np.clip(img, 0, 255).astype(np.uint8)

    # pieces
    rad = int(square_px * 0.36)
    ring_w = max(2, square_px // 20)
    for r in range(8):
        for c in range(8):
            v = grid[r, c]
            if v == Cell.EMPTY:
                continue
            jy = rng.randint(-jitter, jitter + 1) if jitter else 0
            jx = rng.randint(-jitter, jitter + 1) if jitter else 0
            cy = int((r + 0.5) * square_px) + jy
            cx = int((c + 0.5) * square_px) + jx
            fill = WHITE_PIECE if v == Cell.LIGHT else BLACK_PIECE
            ring = WHITE_RING if v == Cell.LIGHT else BLACK_RING
            cv2.circle(img, (cx, cy), rad, fill, -1, cv2.LINE_AA)
            cv2.circle(img, (cx, cy), rad, ring, ring_w, cv2.LINE_AA)

    # smooth lighting gradient (top-left dim, bottom-right bright)
    if lighting > 0:
        gy, gx = np.mgrid[0:n, 0:n]
        g = 1.0 + lighting * ((gx + gy) / (2.0 * n) - 0.5)
        img = np.clip(img.astype(np.float32) * g[..., None], 0, 255).astype(np.uint8)

    # sensor noise
    if noise > 0:
        nse = rng.normal(0.0, noise * 255.0, img.shape).astype(np.float32)
        img = np.clip(img.astype(np.float32) + nse, 0, 255).astype(np.uint8)

    return img
