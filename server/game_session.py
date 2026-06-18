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

import chess
import chess.pgn

from chessmon.camera import CameraGame, RealBoard
from chessmon.board_state import board_to_grid


class Session:
    def __init__(self, table_token, white="White", black="Black", variant="standard",
                 start_fen=None):
        self.table_token = table_token
        self.pair_token = secrets.token_urlsafe(8)
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

    def session_info(self):
        return {"white": self.white, "black": self.black, "variant": self.variant}

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

    def calibrate_start(self, frame):
        """Step 2: with pieces at the start, lock orientation (a1 dark), seed per-square
        colour samples and the baseline reference. Mirrors `tools/live.py newgame`."""
        if self.board_reader is None:
            raise ValueError("send the empty-board frame first")
        t = self.board_reader.calibrate_orientation_auto(frame)
        if t is None:
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
        return {"type": "move.unclear", "reason": "no legal move matches"}

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
        cw, cb = self._pending or (None, None)
        self._pending = None
        rec = {"ply": len(self.moves) + 1, "san": san, "fen": self.game.board.fen(),
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
        return {"table": self.table_token, "variant": self.variant,
                "white": self.white, "black": self.black,
                "fen": self.game.board.fen(),
                "turn": "White" if self.game.board.turn else "Black",
                "moves": self.moves, "result": self.result, "pgn": self.pgn()}
