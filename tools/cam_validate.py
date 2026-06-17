"""Validate the real-camera -> inference integration on the saved frames."""
import os
import sys

import cv2
import numpy as np
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, CameraGame
from chessmon.board_state import board_to_grid, grid_str

OUT = os.path.join(ROOT, "out")


def img(name):
    return cv2.imread(os.path.join(OUT, name))


def main():
    rb = RealBoard(img("empty.png"))
    t = rb.calibrate_orientation(img("start.png"), img("move1.png"), "b2b4")
    print(f"orientation t={t}  color_thr={rb.color_thr:.1f}  edge_thr={rb.edge_thr:.1f}")

    gs = rb.classify(img("start.png"))
    print("start (inference orientation):")
    print(grid_str(gs))
    print(f"start vs standard: {int((gs == board_to_grid(chess.Board())).sum())}/64 squares")

    game = CameraGame()
    print("observe(start) ->", game.observe(gs)[0])
    kind, san, move = game.observe(rb.classify(img("move1.png")))
    print(f"observe(move1) -> {kind}: {san}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
