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


def register_from_pieces(frame):
    """Register the board WITHOUT clearing it (e.g. from the start position). Pieces
    sit in square centres but inner corners live between squares, so the sector
    detector still finds the largest clear central band; we anchor a homography on it,
    then refine ALL 49 inner corners with cornerSubPix (most survive the pieces) and
    re-fit. Returns the homography, or None if no band is found."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    size = None
    for k in (5, 4, 3):
        for cand in ((7, k), (k, 7)):
            if cv2.findChessboardCornersSB(gray, cand, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)[0]:
                size = cand
                break
        if size:
            break
    if size is None:
        return None
    _ok, corners = cv2.findChessboardCornersSB(gray, size, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    src = corners.reshape(-1, 2).astype(np.float32)
    w, h = size
    coff, roff = (7 - w) // 2, (7 - h) // 2               # centre the band in the 7x7
    dst = np.array([[((k % w) + coff + 1) * SQ, ((k // w) + roff + 1) * SQ] for k in range(len(src))],
                   dtype=np.float32)
    H, _ = cv2.findHomography(src, dst)
    grid = np.array([[(c + 1) * SQ, (r + 1) * SQ] for r in range(7) for c in range(7)], np.float32)
    pred = cv2.perspectiveTransform(grid.reshape(-1, 1, 2), np.linalg.inv(H)).astype(np.float32)
    refined = cv2.cornerSubPix(gray, pred.copy(), (9, 9), (-1, -1),
                               (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.01))
    keep = np.linalg.norm(refined.reshape(-1, 2) - pred.reshape(-1, 2), axis=1) < 6.0
    if int(keep.sum()) >= 40:
        H, _ = cv2.findHomography(refined.reshape(-1, 2)[keep], grid[keep])
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


def decide_colour(norm, ref_light, ref_dark, g_light, g_dark, thr):
    """Light/dark for one occupied square. Prefer the per-square learned samples
    (nearest of ref_light/ref_dark); fall back to the global samples, and finally to
    the threshold. `norm` and all references are lighting-normalised luma. Per-square
    references capture local effects a global threshold can't - e.g. specular glare:
    the glared dark piece's own sample is high, so it still matches 'dark'."""
    el = g_light if (ref_light is None or np.isnan(ref_light)) else ref_light
    ed = g_dark if (ref_dark is None or np.isnan(ref_dark)) else ref_dark
    if el is None or ed is None:
        return Cell.LIGHT if norm > thr else Cell.DARK
    return Cell.LIGHT if abs(norm - el) <= abs(norm - ed) else Cell.DARK


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
        # Adaptive per-square piece-colour samples (inference orientation), learned
        # from positions we know - seeded at the start, refined as pieces move. NaN
        # = unseen; classification falls back to the global samples / threshold.
        self.ref_light = np.full((8, 8), np.nan)
        self.ref_dark = np.full((8, 8), np.nan)
        self.global_light = None
        self.global_dark = None

    @classmethod
    def from_start(cls, start_frame, cdiff_thr=16.0, edge_margin=8.0):
        """Build a registered, calibrated board from the START position - no empty
        frame needed. Registers from the pieces, takes real empty references from the
        32 clear middle squares, and borrows the nearest same-colour empty square's
        reference for each occupied square (refined later as pieces move)."""
        self = cls.__new__(cls)
        H = register_from_pieces(start_frame)
        if H is None:
            raise ValueError("could not register from the start position")
        self.H = H
        ws = cv2.warpPerspective(start_frame, H, (N, N))
        self.we = ws
        rois = np.empty((8, 8), object)
        edges = np.zeros((8, 8))
        luma = np.zeros((8, 8))
        for r in range(8):
            for c in range(8):
                roi = _roi(ws, r, c, OCC_ROI)
                rois[r, c] = roi.astype(np.float32)
                edges[r, c] = _edge(roi)
                luma[r, c] = _median_luma(_roi(ws, r, c, COLOR_ROI))
        # the 32 lowest-edge squares are the empty middle of the start position
        empty = np.zeros((8, 8), bool)
        for idx in np.argsort(edges, axis=None)[:32]:
            empty[idx // 8, idx % 8] = True
        # physical square colours from the empty squares' luma checkerboard
        pe = {p: [luma[r, c] for r in range(8) for c in range(8)
                  if empty[r, c] and (r + c) % 2 == p] for p in (0, 1)}
        dark_parity = 0 if np.mean(pe[0]) < np.mean(pe[1]) else 1
        self.dark_sq = np.array([[(r + c) % 2 == dark_parity for c in range(8)] for r in range(8)])
        # lighting bias from the empty squares (per parity); 0 where occupied (unknown)
        self.bias = np.zeros((8, 8))
        for p in (0, 1):
            es = [(r, c) for r in range(8) for c in range(8) if empty[r, c] and (r + c) % 2 == p]
            mean = float(np.mean([luma[r, c] for r, c in es]))
            for r, c in es:
                self.bias[r, c] = luma[r, c] - mean
        # backgrounds: real for empties, nearest same-colour empty borrowed for occupied
        self.bg = np.empty((8, 8), object)
        for r in range(8):
            for c in range(8):
                if empty[r, c]:
                    self.bg[r, c] = rois[r, c]
                else:
                    er, ec = min(((er, ec) for er in range(8) for ec in range(8)
                                  if empty[er, ec] and (er + ec) % 2 == (r + c) % 2),
                                 key=lambda p: abs(p[0] - r) + abs(p[1] - c))
                    self.bg[r, c] = rois[er, ec]
        self.cdiff_thr = cdiff_thr
        self.edge_thr = float(np.max(edges[empty])) + edge_margin
        self.color_thr = 110.0
        self.t = 0
        self.ref_light = np.full((8, 8), np.nan)
        self.ref_dark = np.full((8, 8), np.nan)
        self.global_light = None
        self.global_dark = None
        return self

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
        if occ.any():
            hi, lo = norm[norm > self.color_thr], norm[norm <= self.color_thr]
            self.global_light = float(np.median(hi)) if hi.size else self.color_thr + 30
            self.global_dark = float(np.median(lo)) if lo.size else self.color_thr - 30
        return self.color_thr

    def classify(self, frame):
        """Frame -> three-state grid in inference convention (orientation applied),
        using per-square learned colour samples when available."""
        occ_c, lum_c = self._measure(frame)
        occ = dihedral(occ_c, self.t)
        norm = dihedral(lum_c - self.bias, self.t)        # lighting-normalised, inference orient
        g = empty_grid()
        for r, c in zip(*np.where(occ)):
            g[r, c] = decide_colour(norm[r, c], self.ref_light[r, c], self.ref_dark[r, c],
                                    self.global_light, self.global_dark, self.color_thr)
        return g

    def learn(self, frame, board):
        """Record per-square piece-colour samples from a frame whose position we KNOW
        (the believed board). Only learns squares that read occupied, so a missed
        piece can't poison a reference. Seed it with chess.Board() at the start, then
        call it after each accepted move."""
        truth = board_to_grid(board)
        occ_c, lum_c = self._measure(frame)
        occ = dihedral(occ_c, self.t)
        norm = dihedral(lum_c - self.bias, self.t)
        for r, c in zip(*np.where(occ)):
            if truth[r, c] == Cell.LIGHT:
                self.ref_light[r, c] = norm[r, c]
            elif truth[r, c] == Cell.DARK:
                self.ref_dark[r, c] = norm[r, c]

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
    # Square colours must align exactly (a1 dark); among those parity-valid
    # orientations pick the best occupancy match. This tolerates a noisy start grid
    # (e.g. same-colour pieces missed when references are borrowed, not yet learned).
    aligned = [(int((dihedral(start_grid, t) == target_occ).sum()), t)
               for t in range(8) if np.array_equal(dihedral(dark_sq, t), chess_dark)]
    if not aligned:
        return None
    best_occ, best_t = max(aligned)
    return best_t if best_occ >= 40 else None
