"""Real-camera board reader: registration + robust classification + orientation.

Produces the same three-state grids `inference.MoveInference` consumes, but from a
real overhead frame. Kept separate from `detector.py` (the synthetic-render path
used by the offline tests) because real boards are read by **background
subtraction** against a known-empty frame rather than the synth's edge-only
shadow rejection. Findings validated on a real wooden set (see tools/).

Pipeline per frame:
  warp to canonical (homography from the empty board's inner corners)
  -> for each square, sample only its MIDDLE (avoids grid lines, fold seam and
     the tops of leaning neighbour pieces)
  -> occupied if it changed vs the empty board (per-pixel) OR has edge texture,
     using absolute thresholds just above the empty-board floor
  -> light/dark from the MEDIAN luma of the core (median ignores specular glare)
  -> apply the calibrated board orientation -> grid in inference convention.
"""
from __future__ import annotations

import numpy as np
import cv2
import chess

from .board_state import Cell, board_to_grid, empty_grid, rc_to_square

SQ, N = 80, 640
OCC_ROI, COLOR_ROI = 0.45, 0.30


def _roi(img, r, c, frac):
    m = int(SQ * (1 - frac) / 2)
    return img[r * SQ + m:(r + 1) * SQ - m, c * SQ + m:(c + 1) * SQ - m]


def _edge(x):
    g = cv2.cvtColor(x, cv2.COLOR_BGR2GRAY)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return float(np.mean(np.sqrt(gx * gx + gy * gy)))


def _median_luma(roi):
    b, g, r = roi[..., 0].astype(np.float32), roi[..., 1].astype(np.float32), roi[..., 2].astype(np.float32)
    return float(np.median(0.114 * b + 0.587 * g + 0.299 * r))


def _otsu(vals):
    v = np.asarray(vals, np.float32)
    lo, hi = float(v.min()), float(v.max())
    if hi - lo < 1e-6:
        return hi
    vn = cv2.normalize(v, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    t, _ = cv2.threshold(vn, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return lo + (hi - lo) * t / 255.0


def register(empty_img):
    """Homography mapping the frame to a canonical 640px board, from the empty
    board's 7x7 inner corners (sector detector, with a classic fallback)."""
    gray = cv2.cvtColor(empty_img, cv2.COLOR_BGR2GRAY)
    ok, c = cv2.findChessboardCornersSB(gray, (7, 7), flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        ok, c = cv2.findChessboardCorners(
            gray, (7, 7), flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not ok:
        return None
    src = c.reshape(-1, 2).astype(np.float32)
    dst = np.array([[(j + 1) * SQ, (i + 1) * SQ] for i in range(7) for j in range(7)], np.float32)
    H, _ = cv2.findHomography(src, dst)
    return H


def dihedral(g, t):
    """One of the 8 board orientations. t%4 = number of 90 deg rotations;
    t>=4 adds a left-right flip (the handedness ambiguity)."""
    g2 = np.rot90(g, t % 4)
    return np.fliplr(g2) if t >= 4 else g2


def _square_is_light(r, c):
    """Colour of board square (r,c) in inference-grid coords (a1 is dark)."""
    sq = rc_to_square(r, c)
    return (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1


class RealBoard:
    def __init__(self, empty_img, cdiff_thr=16.0, edge_margin=8.0):
        self.H = register(empty_img)
        if self.H is None:
            raise ValueError("could not register board - need a clear EMPTY frame")
        self.we = cv2.warpPerspective(empty_img, self.H, (N, N))
        self.bg = np.empty((8, 8), object)
        empty_edges = []
        empty_luma = np.zeros((8, 8))
        for r in range(8):
            for c in range(8):
                self.bg[r, c] = _roi(self.we, r, c, OCC_ROI).astype(np.float32)
                empty_edges.append(_edge(_roi(self.we, r, c, OCC_ROI)))
                empty_luma[r, c] = _median_luma(_roi(self.we, r, c, COLOR_ROI))
        # Per-square LIGHTING bias: how much brighter/darker this square's empty
        # reference is than the average for its colour (cream vs red). Subtracting
        # it before the light/dark decision means colour is judged locally, so a
        # piece in a bright spot (or a glare corner) is not mis-read as light.
        self.bias = np.zeros((8, 8))
        for parity in (0, 1):
            idx = [(r, c) for r in range(8) for c in range(8) if (r + c) % 2 == parity]
            mean = float(np.mean([empty_luma[r, c] for r, c in idx]))
            for r, c in idx:
                self.bias[r, c] = empty_luma[r, c] - mean
        # Physical square colours: the darker checkerboard parity is the red/dark
        # squares. Used to enforce the "a1 is a dark square" rule when auto-detecting
        # orientation (a 90 deg board rotation inverts this parity).
        pm = {p: float(np.mean([empty_luma[r, c] for r in range(8) for c in range(8)
                                if (r + c) % 2 == p])) for p in (0, 1)}
        dark_parity = 0 if pm[0] < pm[1] else 1
        self.dark_sq = np.array([[(r + c) % 2 == dark_parity for c in range(8)] for r in range(8)])
        # Occupancy leans on background subtraction (cdiff); edge is only a backup,
        # set ABOVE the empty board's worst square (the fold seam) so it can never
        # fire on an empty square - only genuinely high-texture squares clear it.
        self.cdiff_thr = cdiff_thr
        self.edge_thr = float(np.max(empty_edges)) + edge_margin
        self.color_thr = 110.0      # set by calibrate_color / calibrate_orientation
        self.t = 0                  # orientation transform index (set by calibrate_orientation)

    def warp(self, frame):
        return cv2.warpPerspective(frame, self.H, (N, N))

    def _measure(self, frame):
        w = self.warp(frame)
        occ = np.zeros((8, 8), bool)
        lum = np.zeros((8, 8))
        for r in range(8):
            for c in range(8):
                roi = _roi(w, r, c, OCC_ROI).astype(np.float32)
                cdiff = float(np.mean(np.abs(roi - self.bg[r, c])))
                edge = _edge(_roi(w, r, c, OCC_ROI))
                occ[r, c] = (cdiff > self.cdiff_thr) or (edge > self.edge_thr)
                lum[r, c] = _median_luma(_roi(w, r, c, COLOR_ROI))
        return occ, lum

    def _grid(self, occ, lum, color_thr):
        norm = lum - self.bias                      # lighting-normalised luma
        g = empty_grid()
        g[occ & (norm > color_thr)] = Cell.LIGHT
        g[occ & (norm <= color_thr)] = Cell.DARK
        return g

    def calibrate_color(self, start_frame):
        occ, lum = self._measure(start_frame)
        norm = (lum - self.bias)[occ]
        self.color_thr = _otsu(norm) if occ.any() else 110.0
        return self.color_thr

    def classify(self, frame):
        """Frame -> three-state grid in inference convention (orientation applied)."""
        occ, lum = self._measure(frame)
        return dihedral(self._grid(occ, lum, self.color_thr), self.t)

    def calibrate_orientation(self, start_frame, after_frame, expected_uci, tol=4):
        """Lock the board orientation from a start frame + one known reference move.
        Handles any rotation (incl. 90 deg) plus the left-right handedness the
        symmetric start position cannot reveal alone."""
        self.calibrate_color(start_frame)
        gs = self._grid(*self._measure(start_frame), self.color_thr)
        ga = self._grid(*self._measure(after_frame), self.color_thr)
        self.t = solve_orientation(gs, ga, expected_uci, tol)
        return self.t

    def calibrate_orientation_auto(self, start_frame, tol=4):
        """Lock orientation from the start position + the 'a1 is a dark square' rule -
        no reference move needed. Sets self.t and returns it, or returns None if no
        orientation makes a1 dark (the board is mis-set / rotated 90 deg)."""
        self.calibrate_color(start_frame)
        gs = self._grid(*self._measure(start_frame), self.color_thr)
        t = solve_orientation_by_color(gs, self.dark_sq, tol)
        if t is not None:
            self.t = t
        return t


class CameraGame:
    """Move inference for a real camera. Matches each legal move against the DELTA
    between consecutive observed grids, so stable per-square misreads (e.g. a
    glared square that always reads the wrong colour) cancel out instead of
    breaking the match. Seed with the start frame via the first observe();
    thereafter observe() returns (kind, san, move):
        "baseline" | "nochange" | "move" | "error" | "ambiguous".
    """

    def __init__(self, board: chess.Board | None = None):
        self.board = board.copy() if board is not None else chess.Board()
        self.prev: np.ndarray | None = None

    def observe(self, obs, max_noise=10):
        """Identify the move from the DELTA to the previous frame, using detectability
        + legality (after Haugen): we know each square's colour, so for every legal
        move we can predict whether its effect *should* be visible.

        A move is RULED OUT (hard) if it predicts something we'd reliably see but
        don't - a piece arriving on a CONTRASTING square, or its origin failing to
        vacate. A predicted change we'd be unlikely to see - a same-colour piece on a
        same-colour square - is FORGIVEN ('soft'). Among the survivors we rank by
        (soft, then unrelated flicker); if ONE move best explains what we reliably
        see, we take it - stray flicker doesn't veto a unique legal answer, it only
        breaks ties. Genuinely competing reads are reported ambiguous; a hand over
        the board (huge change) trips `max_noise`."""
        obs = np.asarray(obs)
        if self.prev is None:
            self.prev = obs.copy()
            return ("baseline", None, None)
        if np.array_equal(obs, self.prev):
            return ("nochange", None, None)
        viable = []
        for m in self.board.legal_moves:
            before = board_to_grid(self.board)
            self.board.push(m)
            after = board_to_grid(self.board)
            self.board.pop()
            changed = before != after
            hard, soft = False, 0
            for r, c in zip(*np.where(changed)):
                want, got = after[r, c], obs[r, c]
                if got == want:
                    continue
                if want == Cell.EMPTY:           # origin should have vacated but hasn't
                    hard = True
                    break
                if got == Cell.EMPTY:            # piece expected, square reads empty
                    low_contrast = (want == Cell.LIGHT) == _square_is_light(r, c)
                    if low_contrast:             # same-colour-on-same-colour: forgiven
                        soft += 1
                    else:                        # contrasting: we'd have seen it
                        hard = True
                        break
                else:
                    soft += 1                    # occupied but wrong colour (e.g. glare)
            if hard:
                continue
            unexplained = int(np.count_nonzero((self.prev != obs) & ~changed))
            viable.append((soft, unexplained, m))
        if not viable:
            return ("error", None, None)
        viable.sort(key=lambda v: (v[0], v[1]))
        top = viable[0]
        tied = [v for v in viable if (v[0], v[1]) == (top[0], top[1])]
        if len({(v[2].from_square, v[2].to_square) for v in tied}) > 1:
            return ("ambiguous", None, [self.board.san(v[2]) for v in tied])
        if top[1] > max_noise:                   # too much unexplained change (a hand?)
            return ("error", None, None)
        move = next((v[2] for v in tied if v[2].promotion == chess.QUEEN), top[2])
        san = self.board.san(move)
        self.board.push(move)
        self.prev = obs.copy()
        return ("move", san, move)


def solve_orientation(start_grid, after_grid, expected_uci, tol=4):
    """Find the orientation index t (0-7) under which `start_grid` is the standard
    start (within `tol` squares) and the start->after change is `expected_uci`.
    Grid-level and hardware-free, so it is unit-testable. The move check is
    delta-based, so a stable misread does not derail it."""
    target = board_to_grid(chess.Board())
    cands = [t for t in range(8) if int((dihedral(start_grid, t) == target).sum()) >= 64 - tol]
    for t in (cands or list(range(8))):
        game = CameraGame()
        game.observe(dihedral(start_grid, t))                  # seed baseline
        kind, _san, move = game.observe(dihedral(after_grid, t))
        if kind == "move" and move is not None and move.uci() == expected_uci:
            return t
    return cands[0] if cands else 0


def solve_orientation_by_color(start_grid, dark_sq, tol=4):
    """Determine orientation with NO reference move, using the rule that a1 is a DARK
    square. Pick the transform t under which the start occupancy matches the standard
    start AND the physical square colours line up with the chessboard (so a1 is dark).
    Returns t, or None if no orientation makes a1 dark (board mis-set / rotated 90deg).
    Occupancy fixes which side is White (the rank axis); the colours fix the remaining
    handedness that the symmetric start position can't."""
    target_occ = board_to_grid(chess.Board())
    chess_dark = np.array([[not _square_is_light(r, c) for c in range(8)] for r in range(8)])
    for t in range(8):
        if (int((dihedral(start_grid, t) == target_occ).sum()) >= 64 - tol
                and np.array_equal(dihedral(dark_sq, t), chess_dark)):
            return t
    return None
