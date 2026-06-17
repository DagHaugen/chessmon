"""Maps board squares to image regions in a canonical, axis-aligned board image.

The detector always works on a square, top-down "canonical" board (e.g. 640x640
for 80px squares). For the synthetic renderer the frame already *is* canonical.
For a real camera, `calibrate_camera.warp` rectifies the frame into this same
canonical space first, so the geometry below is identical in both cases.

Two ROIs per square:
  * square ROI (~62% of the square)  -> occupancy / edge test, trimmed to avoid
    bleeding into neighbours and to dodge edge shadows.
  * core ROI   (~30% of the square)  -> colour test, sampling only the piece top.
"""
from __future__ import annotations

import numpy as np


class BoardGeometry:
    def __init__(self, square_px: int, roi_frac: float = 0.62, core_frac: float = 0.30):
        self.square_px = square_px
        self.roi_frac = roi_frac
        self.core_frac = core_frac

    def _roi(self, img: np.ndarray, r: int, c: int, frac: float) -> np.ndarray:
        sp = self.square_px
        m = int(sp * (1.0 - frac) / 2.0)
        y0, x0 = r * sp + m, c * sp + m
        y1, x1 = (r + 1) * sp - m, (c + 1) * sp - m
        return img[y0:y1, x0:x1]

    def square_roi(self, img: np.ndarray, r: int, c: int) -> np.ndarray:
        return self._roi(img, r, c, self.roi_frac)

    def core_roi(self, img: np.ndarray, r: int, c: int) -> np.ndarray:
        return self._roi(img, r, c, self.core_frac)
