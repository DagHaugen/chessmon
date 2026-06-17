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
REF = os.path.join(OUT, "live_ref.png")          # evolving empty-board reference
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


def refs_to_list(a):
    return [[None if np.isnan(v) else round(float(v), 2) for v in row] for row in a]


def refs_from_list(lst):
    return np.array([[np.nan if v is None else float(v) for v in row] for row in lst])


def save_model(s, rb):
    s["color_thr"] = float(rb.color_thr)
    s["global_light"] = rb.global_light
    s["global_dark"] = rb.global_dark
    s["ref_light"] = refs_to_list(rb.ref_light)
    s["ref_dark"] = refs_to_list(rb.ref_dark)


def board_reader():
    s = load() if os.path.exists(STATE) else {}
    rb = (RealBoard.from_start(img("live_start.png")) if s.get("from_start")
          else RealBoard(img("empty.png")))
    if os.path.exists(REF):
        rb.we = cv2.imread(REF)                  # the refined reference from prior moves
    if s:
        rb.t = s["t"]
        rb.color_thr = s["color_thr"]
        rb.global_light = s.get("global_light")
        rb.global_dark = s.get("global_dark")
        if s.get("ref_light"):
            rb.ref_light = refs_from_list(s["ref_light"])
        if s.get("ref_dark"):
            rb.ref_dark = refs_from_list(s["ref_dark"])
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
    frame = capture()
    if frame is None:
        print("no frame (is the Windows Camera app open?)")
        return 1
    rb = RealBoard(img("empty.png"))
    t = rb.calibrate_orientation_auto(frame)        # orientation from colours; a1 must be dark
    if t is None:
        print("a1 is NOT a dark square - the board looks rotated 90 deg. Fix the setup and retry.")
        return 2
    rb.learn(frame, chess.Board())                  # seed per-square colour samples from the start
    grid = rb.classify(frame)
    s = {"t": int(t), "fen": chess.STARTING_FEN, "prev": grid.tolist()}
    save_model(s, rb)
    save(s)
    cv2.imwrite(REF, rb.we)
    print(grid_str(grid))
    print(f"a1 is dark OK (orientation t={t}, colour_thr={rb.color_thr:.1f}) - White to move")
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
        rb.learn(frame, game.board)            # refine per-square colour samples
        rb.update_bg(frame, game.board)        # refine empty references (vacated squares)
        s["prev_fen"] = s["fen"]               # remember pre-move position for `fix`
        s["fen"] = game.board.fen()
        s["prev"] = game.prev.tolist()
        save_model(s, rb)
        save(s)
        cv2.imwrite(REF, rb.we)
        side = "White" if game.board.turn else "Black"
        print(f"MOVE: {san}    (now {side} to move)")
    elif kind == "nochange":
        print("no change detected")
    elif kind == "ambiguous":
        print(f"ambiguous between: {', '.join(extra)} - re-shoot")
    else:
        print("board unclear - no legal move matches; re-check and re-shoot")
    return 0


def cmd_startgame():
    """Calibrate + baseline from the START position, with NO empty board: register
    from the pieces, seed colour samples, set the baseline. The start occupancy is
    approximate (borrowed backgrounds) - legality + refinement are meant to carry it."""
    frame = capture()
    if frame is None:
        print("no frame (is the Windows Camera app open?)")
        return 1
    cv2.imwrite(os.path.join(OUT, "live_start.png"), frame)
    try:
        rb = RealBoard.from_start(frame)
    except ValueError as e:
        print(f"could not register from the pieces ({e}) - make the board fill the frame.")
        return 2
    t = rb.calibrate_orientation_auto(frame)
    if t is None:
        print("could not orient (a1 not dark?) - check the board is set up correctly.")
        return 2
    rb.learn(frame, chess.Board())
    grid = rb.classify(frame)
    s = {"from_start": True, "t": int(t), "fen": chess.STARTING_FEN, "prev": grid.tolist()}
    save_model(s, rb)
    save(s)
    cv2.imwrite(REF, rb.we)
    print(grid_str(grid))
    print(f"registered from pieces, a1 dark OK (t={t}). Baseline set - White to move.")
    print("(start occupancy is approximate; legality + refinement should carry it.)")
    return 0


def cmd_fix():
    """Replace a mis-detected last move: live.py fix <uci>. Rebuilds from the stored
    pre-move position, re-learns colour samples + reference, re-baselines."""
    if len(sys.argv) < 3:
        print("usage: live.py fix <uci>  (e.g. d8d5)")
        return 1
    s = load()
    if "prev_fen" not in s:
        print("no stored pre-move position to correct from.")
        return 1
    board = chess.Board(s["prev_fen"])
    board.push_uci(sys.argv[2])
    rb = board_reader()
    frame = img("live_frame.png")
    rb.learn(frame, board)
    rb.update_bg(frame, board)
    s["fen"] = board.fen()
    s["prev"] = rb.classify(frame).tolist()
    save_model(s, rb)
    save(s)
    cv2.imwrite(REF, rb.we)
    print(f"corrected to {sys.argv[2]}; now {'White' if board.turn else 'Black'} to move")
    return 0


def cmd_commit():
    """Commit a move the detector flagged ambiguous/unclear: live.py commit <uci>.
    Pushes the move onto the CURRENT position (unlike `fix`, which replaces the last)."""
    if len(sys.argv) < 3:
        print("usage: live.py commit <uci>  (e.g. d5e5)")
        return 1
    s = load()
    board = chess.Board(s["fen"])
    board.push_uci(sys.argv[2])
    rb = board_reader()
    frame = img("live_frame.png")
    rb.learn(frame, board)
    rb.update_bg(frame, board)
    s["prev_fen"] = s["fen"]
    s["fen"] = board.fen()
    s["prev"] = rb.classify(frame).tolist()
    save_model(s, rb)
    save(s)
    cv2.imwrite(REF, rb.we)
    print(f"committed {sys.argv[2]}; now {'White' if board.turn else 'Black'} to move")
    return 0


def cmd_gesture():
    """Read the end-of-game gesture: both kings to the centre, result encoded by the
    colour of the squares they stand on (both light=White, both dark=Black, else draw)."""
    s = load()
    rb = board_reader()
    frame = capture()
    if frame is None:
        print("no frame (is the Windows Camera app open?)")
        return 1
    grid = rb.classify(frame)
    print(grid_str(grid))
    # centre squares in inference-grid coords, and their physical colour
    centre = {chess.D4: (4, 3), chess.E4: (4, 4), chess.D5: (3, 3), chess.E5: (3, 4)}
    light_sq = {chess.E4, chess.D5}     # the cream centre squares
    occ_light = sum(grid[rc] != 0 for sq, rc in centre.items() if sq in light_sq)
    occ_dark = sum(grid[rc] != 0 for sq, rc in centre.items() if sq not in light_sq)
    print(f"centre: {occ_light} light-square king(s), {occ_dark} dark-square king(s)")
    if occ_light and not occ_dark:
        print("RESULT: White wins (1-0)")
    elif occ_dark and not occ_light:
        print("RESULT: Black wins (0-1)")
    elif occ_light and occ_dark:
        print("RESULT: draw (1/2-1/2)")
    else:
        print("no kings detected in the centre")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    return {"calibrate": cmd_calibrate, "empty": cmd_empty, "newgame": cmd_newgame,
            "startgame": cmd_startgame, "shot": cmd_shot, "fix": cmd_fix,
            "commit": cmd_commit, "gesture": cmd_gesture}.get(
                cmd, lambda: (print(__doc__), 1)[1])()


if __name__ == "__main__":
    sys.exit(main())
