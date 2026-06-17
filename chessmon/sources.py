"""Frame sources. Only the webcam source needs hardware; it is isolated here so
the rest of the package can be exercised entirely from the synthetic renderer.
"""
from __future__ import annotations

import cv2


class WebcamSource:
    def __init__(self, index: int = 0):
        self.cap = cv2.VideoCapture(index)
        if not self.cap.isOpened():
            raise RuntimeError(f"could not open webcam index {index}")

    def read(self):
        ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        self.cap.release()
