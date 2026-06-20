"""Pure game-session logic for the chessmon server.

Wraps chessmon's `CameraGame` (move inference) and `RealBoard` (camera calibration),
records the move list + reported clocks, and maps each detection verdict to a wire
message. There is NO socket / async / FastAPI here on purpose — the I/O layer (`app.py`)
owns the sockets — so this stays hardware-free and unit-testable (see tests/test_server.py).

Verdict mapping (what `CameraGame.observe` returns -> what the clock unit receives):
    move      -> move.result {san, fen, ply, turn}
    ambiguous -> move.ambiguous {candidates}
    unseen    -> move.unseen {candidates}
    gesture   -> game.end {result, pgn}        (kings-to-centre)
    nochange  -> move.nochange
    error     -> move.unclear {reason}
    baseline  -> session.baselined             (first grid seeds the reference)
The clock resolves ambiguous/unseen by sending move.resolve {uci} -> resolve().
"""
from __future__ import annotations

import secrets
import time

import numpy as np
import chess
import chess.pgn

from chessmon.camera import CameraGame, RealBoard, solve_orientation_by_color
from chessmon.board_state import Cell, board_to_grid, empty_grid


class Session:
    def __init__(self, table_token, white="White", black="Black", variant="standard",
                 start_fen=None, name=""):
        self.table_token = table_token
        self.pair_token = secrets.token_urlsafe(8)
        self.name = name                   # user-friendly table name from the console ("Table 1")
        self.clock_dev = None              # devId of the assigned clock / camera unit (the table config,
        self.camera_dev = None             # persisted so it survives the units going offline)
        self.started_at = None             # epoch seconds of the first move (a "running game")
        self.corners = None                # last calibration corners (fractions 0..1) so the console can re-show / edit them
        self.white, self.black, self.variant = white, black, variant
        if start_fen:
            board = chess.Board(start_fen)
        else:
            board = chess.Board(chess960=(variant == "chess960"))
        self.game = CameraGame(board)
        self.board_reader: RealBoard | None = None
        self.moves: list[dict] = []        # [{ply, san, fen, clock_white, clock_black}]
        self.result: str | None = None
        self._pending = None               # clocks from move.confirm, applied on next accept
        self._last_grid = None             # grid that produced the last verdict (for resolve)
        self._calib_step = None            # next binary frame is this calibration step
        self._calib_frame = None           # empty-board frame relayed to the clock for corner-tap

    def __getstate__(self):                # persistence: drop the big transient calibration frame
        d = self.__dict__.copy()
        d["_calib_frame"] = None
        return d

    def __setstate__(self, d):             # tolerate older pickles that predate newer fields
        self.__dict__.update(d)
        for k, v in (("clock_dev", None), ("camera_dev", None), ("started_at", None), ("name", ""), ("corners", None)):
            if not hasattr(self, k):
                setattr(self, k, v)

    def session_info(self):
        return {"name": self.name, "white": self.white, "black": self.black, "variant": self.variant}

    # --- calibration: the real-camera path, reusing chessmon's no-empty-board flow ---
    def calibrate_from_frame(self, frame):
        """Register from the pieces at the start position, lock orientation, seed the
        baseline reference. Mirrors `tools/live.py startgame`. (Exposure-lock + empty-board
        calibration are camera-unit concerns; see the chessmon memory.)"""
        rb = RealBoard.from_start(frame)
        t = rb.calibrate_orientation_auto(frame)
        if t is None:
            raise ValueError("could not orient the board (a1 not dark?)")
        rb.learn(frame, chess.Board())
        self.board_reader = rb
        self.seed_baseline(rb.classify(frame))
        return {"t": int(t)}

    def calibrate_empty(self, frame):
        """Step 1 (robust, empty-board): register the bare board -> true references for
        every square. Mirrors `tools/live.py empty`."""
        self.board_reader = RealBoard(frame)

    def calibrate_empty_corners(self, frame, corners):
        """Step 1 (manual, glare-proof): register the empty board from 4 tapped OUTER corners
        instead of detecting the checkerboard. `corners` are pixel (x, y) in the frame, any
        order. Used by the clock's drag-the-corners UI -> works on any board / lighting."""
        self.board_reader = RealBoard(frame, corners=corners)
        return {"type": "calib.ok", "step": "corners"}

    def calibrate_oneshot(self, frame, corners):
        """ONE-STEP calibration on the SET-UP start position: register from the 4 tapped corners,
        borrow references from the clear centre (from_start), then orient. If colour can pick
        White automatically it baselines; otherwise the start is symmetric, so it returns
        `orient.ask` and the clock asks the operator which side White is on."""
        rb = RealBoard.from_start(frame, corners=corners)
        self.board_reader = rb
        self._calib_frame = frame
        t = rb.calibrate_orientation_auto(frame)
        if t is not None:
            rb.learn(frame, self.game.board)
            self.seed_baseline(rb.classify(frame))
            return {"type": "session.baselined", "t": int(t)}
        return {"type": "orient.ask"}

    def resolve_orientation(self, side):
        """Finish one-step calibration once the clock says which IMAGE side White's pieces are on
        ('top'/'bottom'/'left'/'right'). Colour is now user-asserted (not detected), so the solve
        is reliable. Locks orientation and seeds the baseline."""
        rb = self.board_reader
        if rb is None or self._calib_frame is None:
            return {"type": "calib.failed", "reason": "calibrate first"}
        t = solve_orientation_by_color(self._asserted_start(side), rb.dark_sq)
        if t is None:
            return {"type": "calib.failed", "reason": "couldn't orient - is it a standard start position?"}
        rb.t = t
        rb.learn(self._calib_frame, self.game.board)
        self.seed_baseline(rb.classify(self._calib_frame))
        return {"type": "session.baselined", "t": int(t)}

    def _asserted_start(self, side):
        """Idealised start grid in CANONICAL (warp) orientation: White's 16 pieces (LIGHT) on the
        chosen image side, Black's (DARK) opposite, centre empty. Feeds the colour solver."""
        g = empty_grid()
        near, far = (slice(0, 2), slice(6, 8)) if side in ("top", "left") else (slice(6, 8), slice(0, 2))
        if side in ("left", "right"):
            g[:, near], g[:, far] = Cell.LIGHT, Cell.DARK
        else:
            g[near, :], g[far, :] = Cell.LIGHT, Cell.DARK
        return g

    def calibrate_start(self, frame):
        """Step 2: with pieces at the start, lock orientation (a1 dark), seed per-square
        colour samples and the baseline reference. Mirrors `tools/live.py newgame`."""
        if self.board_reader is None:
            raise ValueError("send the empty-board frame first")
        t = self.board_reader.calibrate_orientation_auto(frame)
        if t is None:
            occ = int((self.board_reader.classify(frame) != 0).sum())
            if occ < 8:                      # board reads ~empty -> the 'empty' reference had pieces
                raise ValueError("the board reads as empty - the empty-board step was done with the "
                                 "pieces still on it. Clear the board, re-tap the corners, then "
                                 "capture the start position.")
            raise ValueError("a1 is not a dark square (board rotated 90 deg?)")
        self.board_reader.learn(frame, chess.Board())
        self.seed_baseline(self.board_reader.classify(frame))
        return {"t": int(t)}

    def set_calib_step(self, step):
        self._calib_step = step

    def on_frame(self, frame):
        """Dispatch one decoded camera frame: a calibration step, or a move."""
        step, self._calib_step = self._calib_step, None
        try:
            if step == "empty":
                self.calibrate_empty(frame)
                return {"type": "calib.ok", "step": "empty"}
            if step == "start":
                return {"type": "session.baselined", **self.calibrate_start(frame)}
            if step == "refresh":
                return self.resnap(frame)
            if self.board_reader is None:
                return {"type": "calib.failed", "reason": "camera not calibrated"}
            return self.ingest_frame(frame)
        except Exception as e:
            return {"type": "calib.failed", "reason": str(e)}

    def seed_baseline(self, grid):
        """Set the detector's reference grid (the observed start position)."""
        self.game.prev = None
        self.game.observe(grid)            # first observe just stores the baseline
        self._last_grid = grid

    def resnap(self, frame):
        """Re-anchor the detector to the CURRENT believed position WITHOUT making a move — for when a
        piece was nudged or the board / lighting drifted. Re-learns the colour + background references
        (learn skips invisible same-colour pieces and update_bg never bakes in an occupied square, so
        this is safe even on a low-contrast set) and re-seeds the baseline, so the next move's delta is
        measured from the board exactly as it sits now."""
        if self.board_reader is None:
            return {"type": "calib.failed", "reason": "camera not calibrated"}
        self.board_reader.learn(frame, self.game.board)
        self.board_reader.update_bg(frame, self.game.board)
        self.seed_baseline(self.board_reader.classify(frame))
        return {"type": "refreshed", "fen": self.game.board.fen()}

    def reset_game(self):
        """Operator moved all pieces back to the start position and confirmed RESET: rebuild the game
        from scratch. The detector re-anchors to the start position from the next camera frame, which
        the I/O layer requests as a 'refresh' (resnap against the now-start board)."""
        self.game = CameraGame(chess.Board(chess960=(self.variant == "chess960")))
        self.moves = []
        self.result = None
        self.started_at = None
        self._pending = None

    def mark_started(self):
        """Clock pressed START -> the game counts as 'running' even before the first move lands."""
        if self.started_at is None:
            self.started_at = time.time()

    def undo_move(self):
        """Take back the last accepted move (a misread / wrong move). Pops the game + move list; the
        detector re-anchors to the reverted position from the next camera frame (a 'refresh')."""
        if not self.game.board.move_stack:
            return False
        self.game.board.pop()
        if self.moves:
            self.moves.pop()
        self.result = None
        self._pending = None
        return True

    # --- move loop ---
    def confirm(self, side, clock_white=None, clock_black=None):
        """A player tapped CONFIRM. Stash the reported clocks; the camera frame follows."""
        self._pending = (clock_white, clock_black)

    def ingest_frame(self, frame):
        """Server-side vision: classify the uploaded frame, then run inference."""
        if self.board_reader is None:
            return {"type": "move.unclear", "reason": "camera not calibrated"}
        return self.ingest_grid(self.board_reader.classify(frame), frame=frame)

    def ingest_grid(self, grid, frame=None):
        """Run one detection step against the observed occupancy grid."""
        self._last_grid = grid
        kind, san, extra = self.game.observe(grid)
        if kind == "move":
            if frame is not None and self.board_reader is not None:
                self.board_reader.learn(frame, self.game.board)
                self.board_reader.update_bg(frame, self.game.board)
            return self._record(san)
        if kind == "gesture":
            self.result = san
            return {"type": "game.end", "result": san, "pgn": self.pgn()}
        if kind == "ambiguous":
            return {"type": "move.ambiguous", "candidates": self._cands(extra)}
        if kind == "unseen":
            return {"type": "move.unseen", "candidates": self._cands(extra)}
        if kind == "nochange":
            return {"type": "move.nochange"}
        if kind == "baseline":
            return {"type": "session.baselined"}
        if kind == "error":                            # a change that fits no legal move -> flag it
            return {"type": "move.unclear", "reason": san or "no legal move matches",
                    "squares": self._delta_squares(extra)}
        return {"type": "move.unclear", "reason": "no legal move matches"}

    def _delta_squares(self, delta):
        """Map a boolean change-mask (board_to_grid coords: row 0 = rank 8, col 0 = file a) to chess
        square names, so the clock can flag exactly the squares the illegal move touched."""
        if delta is None:
            return []
        return [chr(97 + int(c)) + str(8 - int(r)) for r, c in zip(*np.where(np.asarray(delta)))]

    def _cands(self, sans):
        """Attach the UCI to each SAN candidate so the clock can resolve by tapping one."""
        out = []
        for s in sans or []:
            try:
                out.append({"san": s, "uci": self.game.board.parse_san(s).uci()})
            except Exception:
                out.append({"san": s, "uci": None})
        return out

    def resolve(self, uci):
        """Commit a move the detector flagged ambiguous/unseen (the player tapped it on
        the clock). Re-baselines the detector to the grid that prompted the prompt."""
        move = chess.Move.from_uci(uci)
        san = self.game.board.san(move)
        self.game.board.push(move)
        if self._last_grid is not None:
            self.game.prev = self._last_grid
        return self._record(san)

    def end(self, result):
        self.result = result
        return {"type": "game.end", "result": result, "pgn": self.pgn()}

    def _record(self, san):
        if self.started_at is None:
            self.started_at = time.time()      # first move -> the game is now "running"
        cw, cb = self._pending or (None, None)
        self._pending = None
        rec = {"ply": len(self.moves) + 1, "san": san, "fen": self.game.board.fen(),
               "uci": self.game.board.peek().uci() if self.game.board.move_stack else None,
               "clock_white": cw, "clock_black": cb}
        self.moves.append(rec)
        if self.game.board.is_game_over():
            self.result = self.game.board.result()
        turn = "White" if self.game.board.turn else "Black"
        return {"type": "move.result", "turn": turn, **rec}

    def pgn(self):
        g = chess.pgn.Game.from_board(self.game.board)
        g.headers["White"], g.headers["Black"] = self.white, self.black
        if self.result:
            g.headers["Result"] = self.result
        exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
        return g.accept(exporter).strip()

    def snapshot(self):
        return {"table": self.table_token, "variant": self.variant, "name": self.name,
                "white": self.white, "black": self.black,
                "fen": self.game.board.fen(),
                "turn": "White" if self.game.board.turn else "Black",
                "calibrated": self.board_reader is not None,
                "moves": self.moves, "result": self.result, "pgn": self.pgn()}
