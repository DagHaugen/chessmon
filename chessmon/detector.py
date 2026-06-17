"""Vision side: a canonical (rectified) frame -> three-state grid.

Design notes
------------
Occupancy is decided on EDGE ENERGY, not brightness difference. A real piece
(or our rendered disc-with-ring) injects strong gradients; a flat empty square
and a soft cast shadow do not. Using edge energy as the primary cue therefore
  * rejects shadows (the classic false-positive), and
  * still catches the low-contrast "white piece on a white square" case, whose
    silhouette/ring produces edges even when the mean colour barely changes.

Colour (light vs dark piece) is read from the small core ROI - the piece top -
so it is largely independent of the square colour underneath.

All thresholds are *calibrated from two reference frames* (an empty board and
the known standard start position), never hard-coded. The same calibration runs
verbatim against a real webcam.
"""
from __future__ import annotations

import numpy as np
import cv2
import chess

from .board_state import Cell, empty_grid, square_to_rc
from .geometry import BoardGeometry


def _luma(bgr: np.ndarray) -> float:
    return float(0.114 * bgr[..., 0] + 0.587 * bgr[..., 1] + 0.299 * bgr[..., 2])


def _edge_energy(roi: np.ndarray) -> float:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(np.sqrt(gx * gx + gy * gy)))


class Calibration:
    """Per-square references + thresholds learned from two reference frames."""

    def __init__(self, geom, empty_bg, edge_thresh, color_thresh):
        self.geom = geom
        self.empty_bg = empty_bg          # (8,8,3) mean BGR of each empty square
        self.edge_thresh = edge_thresh    # occupancy decision threshold
        self.color_thresh = color_thresh  # light/dark luma split

    @classmethod
    def from_references(cls, empty_img, start_img, square_px,
                        start_board: chess.Board | None = None) -> "Calibration":
        geom = BoardGeometry(square_px)
        empty_bg = np.zeros((8, 8, 3), dtype=np.float32)
        empty_edge = np.zeros((8, 8), dtype=np.float32)
        for r in range(8):
            for c in range(8):
                roi = geom.square_roi(empty_img, r, c)
                empty_bg[r, c] = roi.reshape(-1, 3).mean(0)
                empty_edge[r, c] = _edge_energy(roi)

        if start_board is None:
            start_board = chess.Board()
        white_luma, black_luma, piece_edge = [], [], []
        for sq in chess.SQUARES:
            p = start_board.piece_at(sq)
            if p is None:
                continue
            r, c = square_to_rc(sq)
            core = geom.core_roi(start_img, r, c).reshape(-1, 3).mean(0)
            (white_luma if p.color == chess.WHITE else black_luma).append(_luma(core))
            piece_edge.append(_edge_energy(geom.square_roi(start_img, r, c)))

        color_thresh = (float(np.median(white_luma)) + float(np.median(black_luma))) / 2.0
        # occupancy threshold: midway between "empty" and "piece" edge evidence
        edge_thresh = (float(np.median(empty_edge)) + float(np.median(piece_edge))) / 2.0
        return cls(geom, empty_bg, edge_thresh, color_thresh)

    def classify(self, warped_img) -> tuple[np.ndarray, np.ndarray]:
        """Return (grid, confidence). Confidence in [0,1], higher = more certain."""
        g = empty_grid()
        conf = np.zeros((8, 8), dtype=np.float32)
        for r in range(8):
            for c in range(8):
                roi = self.geom.square_roi(warped_img, r, c)
                edge = _edge_energy(roi)
                if edge <= self.edge_thresh:
                    g[r, c] = Cell.EMPTY
                    denom = max(1e-6, self.edge_thresh)
                    conf[r, c] = min(1.0, (self.edge_thresh - edge) / denom)
                    continue
                core = self.geom.core_roi(warped_img, r, c).reshape(-1, 3).mean(0)
                l = _luma(core)
                g[r, c] = Cell.LIGHT if l > self.color_thresh else Cell.DARK
                conf[r, c] = min(1.0, abs(l - self.color_thresh) / 128.0)
        return g, conf


class StabilityGate:
    """Emits True exactly once when the board settles after motion.

    This is what makes hands, mid-move transients and j'adoube wobble invisible
    to the classifier: we only ever classify a settled, hand-free frame, and we
    require fresh motion before the next settle so a long-static board fires
    only one observation.
    """

    def __init__(self, settle_frames: int = 5, motion_thresh: float = 8.0):
        self.settle_frames = settle_frames
        self.motion_thresh = motion_thresh
        self.prev: np.ndarray | None = None
        self.still = 0
        self.armed = True  # require motion before the next settle can fire

    def update(self, gray: np.ndarray) -> bool:
        settled_now = False
        if self.prev is not None:
            d = float(np.mean(cv2.absdiff(gray, self.prev)))
            if d > self.motion_thresh:
                self.still = 0
                self.armed = True
            else:
                self.still += 1
                if self.armed and self.still >= self.settle_frames:
                    settled_now = True
                    self.armed = False
        self.prev = gray
        return settled_now
