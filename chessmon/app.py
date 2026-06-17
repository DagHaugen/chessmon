"""Command-line entry points.

    python -m chessmon.app selftest          run the hardware-free game test
    python -m chessmon.app render <FEN>       save a synthetic frame to out/
    python -m chessmon.app webcam [--index N] live monitoring (needs a webcam)
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import chess

from . import synth
from .board_state import board_to_grid, empty_grid, grid_str
from .detector import Calibration, StabilityGate
from .inference import MoveInference

SQUARE_PX = 80
# "camera-like" render settings reused for calibration and the self-test so the
# detector is exercised against noise, uneven lighting, soft shadows and jitter.
RENDER_KW = dict(noise=0.015, lighting=0.25, shadow=True, jitter=2)

# Ruy Lopez Exchange - six captures plus kingside castling, so the three-state
# capture recovery and the castling pattern are both exercised.
GAME = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Bxc6", "dxc6", "O-O", "f6",
        "d4", "exd4", "Nxd4", "c5", "Nb3", "Qxd1", "Rxd1", "Bd7"]


def build_calibration() -> Calibration:
    empty_img = synth.render(empty_grid(), SQUARE_PX, seed=1, **RENDER_KW)
    start_img = synth.render(board_to_grid(chess.Board()), SQUARE_PX, seed=2, **RENDER_KW)
    return Calibration.from_references(empty_img, start_img, SQUARE_PX)


def selftest() -> bool:
    calib = build_calibration()
    truth = chess.Board()
    inf = MoveInference()
    ok = 0
    print("ply  expected  reported  kind        result")
    print("---  --------  --------  ----------  ------")
    for i, san in enumerate(GAME):
        move = truth.parse_san(san)
        truth.push(move)
        frame = synth.render(board_to_grid(truth), SQUARE_PX, seed=100 + i, **RENDER_KW)
        grid, _conf = calib.classify(frame)
        res = inf.observe(grid)
        good = (res.move == move)
        ok += int(good)
        print(f"{i + 1:>3}  {san:<8}  {str(res.san):<8}  {res.kind:<10}  "
              f"{'OK' if good else 'FAIL'}")
        if not good:
            print("    note:", res.note)
    print(f"\n{ok}/{len(GAME)} moves recovered")
    return ok == len(GAME)


def render_fen(fen: str) -> None:
    import os
    import cv2
    board = chess.Board(fen)
    img = synth.render(board_to_grid(board), SQUARE_PX, seed=0, **RENDER_KW)
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "render.png")
    if not cv2.imwrite(path, img):
        raise RuntimeError(f"failed to write {path}")
    print(grid_str(board_to_grid(board)))
    print("wrote", path)


def webcam(index: int) -> None:  # pragma: no cover - needs hardware
    import cv2
    from .sources import WebcamSource
    from .calibrate_camera import find_board_homography, warp

    src = WebcamSource(index)
    print("Calibration: show the EMPTY board, then press SPACE.")
    H = _grab_until_space(src, "empty")
    Hmat = find_board_homography(H)
    if Hmat is None:
        print("Could not find the board grid; ensure the empty board fills the view.")
        return
    empty_warp = warp(H, Hmat, SQUARE_PX)

    print("Now set up the STANDARD START position, then press SPACE.")
    start_raw = _grab_until_space(src, "start")
    start_warp = warp(start_raw, Hmat, SQUARE_PX)

    calib = Calibration.from_references(empty_warp, start_warp, SQUARE_PX)
    gate = StabilityGate()
    inf = MoveInference()
    print("Monitoring. Make moves on the board; press ESC in the window to quit.")
    while True:
        frame = src.read()
        if frame is None:
            break
        rect = warp(frame, Hmat, SQUARE_PX)
        gray = cv2.cvtColor(rect, cv2.COLOR_BGR2GRAY)
        if gate.update(gray):
            grid, _conf = calib.classify(rect)
            res = inf.observe(grid)
            if res.kind == "move":
                print("move:", res.san, ("(" + res.note + ")") if res.note else "")
            elif res.kind in ("error", "ambiguous"):
                print(f"[{res.kind}] {res.note} - please verify the board")
        cv2.imshow("chessmon", rect)
        if cv2.waitKey(1) & 0xFF == 27:
            break
    src.release()
    cv2.destroyAllWindows()


def _grab_until_space(src, title):  # pragma: no cover - needs hardware
    import cv2
    while True:
        frame = src.read()
        if frame is None:
            continue
        cv2.imshow(title, frame)
        if cv2.waitKey(1) & 0xFF == 32:  # SPACE
            cv2.destroyWindow(title)
            return frame


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="chessmon")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("selftest")
    pr = sub.add_parser("render")
    pr.add_argument("fen")
    pw = sub.add_parser("webcam")
    pw.add_argument("--index", type=int, default=0)
    pc = sub.add_parser("clock")
    pc.add_argument("--port", type=int, default=8000)
    pc.add_argument("--base", type=float, default=300.0, help="base seconds per side")
    pc.add_argument("--increment", type=float, default=3.0, help="Fischer increment")
    pc.add_argument("--fen", default=None, help="start from a position (for demos)")
    pc.add_argument("--chess960", action="store_true", help="Fischer Random (Chess960)")
    pc.add_argument("--position", type=int, default=None,
                    help="Chess960 start position 0-959 (default: random)")
    pc.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)

    if args.cmd == "selftest":
        return 0 if selftest() else 1
    if args.cmd == "render":
        render_fen(args.fen)
        return 0
    if args.cmd == "webcam":
        webcam(args.index)
        return 0
    if args.cmd == "clock":
        from .clock_server import run_server
        if not args.no_browser:
            import threading
            import webbrowser
            threading.Timer(0.8, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}/")).start()
        run_server(port=args.port, base_seconds=args.base, increment=args.increment,
                   start_fen=args.fen, chess960=args.chess960, position=args.position)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
