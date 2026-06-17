"""Drive a real game from the BRIO, turn by turn (state persists between calls).

    python tools/live.py calibrate   # one-time: solve orientation from saved frames
    python tools/live.py newgame     # board is at the start position -> set baseline
    python tools/live.py shot        # after a move -> capture, classify, report the move

Uses out/empty.png for registration (re-run if the board/camera moves). Calibrate
uses out/start.png + out/move1.png + the known reference move b2b4.
"""
import json
import os
import sys
import time

import cv2
import numpy as np
import chess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from chessmon.camera import RealBoard, CameraGame
from chessmon.board_state import grid_str

OUT = os.path.join(ROOT, "out")
STATE = os.path.join(OUT, "live.json")
CAM = 1
MJPG = cv2.VideoWriter_fourcc(*"MJPG")


def img(name):
    return cv2.imread(os.path.join(OUT, name))


def capture(idx=CAM):
    cap = cv2.VideoCapture(idx, cv2.CAP_MSMF)
    cap.set(cv2.CAP_PROP_FOURCC, MJPG)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    frame = None
    for _ in range(25):
        cap.grab()
        ok, f = cap.retrieve()
        if ok and f is not None and f.size > 0:
            frame = f
            break
        time.sleep(0.08)
    cap.release()
    if frame is not None:
        cv2.imwrite(os.path.join(OUT, "live_frame.png"), frame)
    return frame


def load():
    with open(STATE) as f:
        return json.load(f)


def save(s):
    with open(STATE, "w") as f:
        json.dump(s, f)


def board_reader():
    rb = RealBoard(img("empty.png"))
    if os.path.exists(STATE):
        s = load()
        rb.t = s["t"]
        rb.color_thr = s["color_thr"]
    return rb


def cmd_calibrate():
    rb = RealBoard(img("empty.png"))
    t = rb.calibrate_orientation(img("start.png"), img("move1.png"), "b2b4")
    save({"t": int(t), "color_thr": float(rb.color_thr),
          "fen": chess.STARTING_FEN, "prev": None})
    print(f"calibrated: orientation t={t}, colour_thr={rb.color_thr:.1f}")
    return 0


def cmd_empty():
    """Capture a fresh EMPTY board: re-registers at the current position/lighting."""
    frame = capture()
    if frame is None:
        print("no frame (is the Windows Camera app open?)")
        return 1
    cv2.imwrite(os.path.join(OUT, "empty.png"), frame)
    try:
        RealBoard(frame)
    except ValueError as e:
        print(f"captured, but registration FAILED ({e}).")
        print("make the empty board fill the frame, fairly square-on, then retry.")
        return 2
    print("empty board captured and registered OK")
    return 0


def cmd_newgame():
    rb = board_reader()
    frame = capture()
    if frame is None:
        print("no frame (is the Windows Camera app open?)")
        return 1
    rb.calibrate_color(frame)                       # refresh colour split for current light
    grid = rb.classify(frame)
    s = load()
    s["color_thr"] = float(rb.color_thr)
    s["fen"] = chess.STARTING_FEN
    s["prev"] = grid.tolist()
    save(s)
    print(grid_str(grid))
    print(f"baseline set (colour_thr={rb.color_thr:.1f}) - White to move")
    return 0


def cmd_shot():
    s = load()
    rb = board_reader()
    frame = capture()
    if frame is None:
        print("no frame (is the Windows Camera app open?)")
        return 1
    game = CameraGame(chess.Board(s["fen"]))
    game.prev = np.array(s["prev"], dtype=np.uint8) if s["prev"] is not None else None
    kind, san, extra = game.observe(rb.classify(frame))
    if kind == "move":
        s["fen"] = game.board.fen()
        s["prev"] = game.prev.tolist()
        save(s)
        side = "White" if game.board.turn else "Black"
        print(f"MOVE: {san}    (now {side} to move)")
    elif kind == "nochange":
        print("no change detected")
    elif kind == "ambiguous":
        print(f"ambiguous between: {', '.join(extra)} - re-shoot")
    else:
        print("board unclear - no legal move matches; re-check and re-shoot")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    return {"calibrate": cmd_calibrate, "empty": cmd_empty, "newgame": cmd_newgame,
            "shot": cmd_shot}.get(cmd, lambda: (print(__doc__), 1)[1])()


if __name__ == "__main__":
    sys.exit(main())
