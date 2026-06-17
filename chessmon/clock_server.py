"""The 'chess clock' device, simulated on the PC as a local web app.

Two layers:
  * ClockGame - pure game/clock logic, fully unit-testable with an injected
    clock (no sockets, no browser, no real time).
  * a tiny stdlib http.server front end that serves the device page and a JSON
    API. No third-party web dependencies.

Concept being prototyped
------------------------
The device is the move-commit signal. The flow per move is:
  1. a player physically makes a move on the board (here: the on-screen board),
  2. the player taps CONFIRM on their side of the clock,
  3. *that tap* triggers the camera read -> detector -> inference,
  4. the move is reported and the clock switches sides.

For a promotion the CONFIRM button is replaced by piece buttons (Q dominant,
then R/B/N). Tapping one resolves the single thing colour-only vision cannot
see - the promoted piece type - via MoveInference.resolve_promotion().

Because there is no camera yet, step 3 reads a *synthetic* render of the current
board, so the real detector and inference run exactly as they will on hardware.
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import chess

from . import synth
from .board_state import Cell, board_to_grid, square_to_rc
from .detector import Calibration
from .inference import MoveInference

SQUARE_PX = 80
RENDER_KW = dict(noise=0.015, lighting=0.25, shadow=True, jitter=2)

_PROMO = {"q": chess.QUEEN, "r": chess.ROOK, "b": chess.BISHOP, "n": chess.KNIGHT}
_COLOR = {chess.WHITE: "white", chess.BLACK: "black"}

# The four central squares. Both kings parked here is an *illegal* chess position
# (kings can never be mutually adjacent, and two pieces can't move in one turn),
# so it can never arise in real play - which makes it a perfect, unambiguous
# "players agree the game is over" gesture for the camera to recognise.
_CENTER = (chess.D4, chess.E4, chess.D5, chess.E5)


def _is_light_square(sq: int) -> bool:
    return (chess.square_file(sq) + chess.square_rank(sq)) % 2 == 1


# The kings' placement also encodes the RESULT, read from the colour of the
# squares they stand on: both kings on light squares -> White won; both on dark
# squares -> Black won; a mixed pair -> draw. In the centre the light squares are
# e4/d5 and the dark squares are d4/e5. Each entry is (white_king_sq, black_king_sq)
# for the simulator to stage; real players just place the kings on those colours.
_RESULT_ARRANGEMENT = {
    "white": (chess.E4, chess.D5),   # both light  -> 1-0
    "black": (chess.D4, chess.E5),   # both dark   -> 0-1
    "draw":  (chess.E4, chess.E5),   # mixed       -> 1/2-1/2
}


def _build_calibration() -> Calibration:
    empty = synth.render(board_to_grid(chess.Board("8/8/8/8/8/8/8/8 w - - 0 1")),
                         SQUARE_PX, seed=1, **RENDER_KW)
    start = synth.render(board_to_grid(chess.Board()), SQUARE_PX, seed=2, **RENDER_KW)
    return Calibration.from_references(empty, start, SQUARE_PX)


class ClockGame:
    """Headless game + clock. Drive it with make_move/confirm/promote and tick."""

    def __init__(self, base_seconds: float = 300.0, increment: float = 3.0,
                 start_fen: str | None = None, chess960: bool = False,
                 position: int | None = None):
        self.base = float(base_seconds)
        self.increment = float(increment)
        self.calib = _build_calibration()
        # Variant. Chess960 only shuffles the back rank, so the start occupancy /
        # colour pattern (ranks 1-2 white, 7-8 black) is identical to standard -
        # calibration and the whole vision path are unchanged. python-chess does
        # the 960-aware legal moves + castling; inference's projection-match just
        # works on whatever board it's given.
        self.position960: int | None = None
        if start_fen is not None:
            self.physical = chess.Board(start_fen, chess960=chess960)
        elif chess960:
            self.position960 = random.randint(0, 959) if position is None else position
            self.physical = chess.Board.from_chess960_pos(self.position960)
        else:
            self.physical = chess.Board()
        self.variant = "chess960" if chess960 else "standard"
        self.inf = MoveInference(self.physical.copy(), interactive_promotion=True)
        self.active = self.physical.turn
        self.times = {chess.WHITE: self.base, chess.BLACK: self.base}
        self.mode = "play"           # "play" | "promotion" | "gameover"
        self.staged = False          # a physical move awaits confirmation
        self.history: list[str] = []
        self.move_elapsed: list[float] = []           # seconds-since-start at each ply
        self.positions: list[str] = [self.physical.board_fen()]  # FEN after each ply (idx 0 = start)
        self.elapsed = 0.0                            # total game time elapsed
        self.start_wall: float | None = None          # wall-clock at first tick (= game start)
        self.report = "Make a move on the board, then tap your clock."
        self.detected = board_to_grid(self.physical)
        self.winner: bool | None = None
        self.result: str | None = None     # "1-0" | "0-1" | "1/2-1/2" once decided
        self._last_tick: float | None = None

    # -- clock -----------------------------------------------------------------
    def tick(self, now: float) -> None:
        if self._last_tick is None:
            self._last_tick = now
            if self.start_wall is None:
                self.start_wall = now
            return
        dt = max(0.0, now - self._last_tick)
        self._last_tick = now
        if self.mode == "gameover":
            return
        self.elapsed += dt
        self.times[self.active] -= dt
        if self.times[self.active] <= 0:
            self.times[self.active] = 0.0
            self.winner = not self.active
            self.mode = "gameover"
            self.report = f"{_COLOR[self.active].title()} flagged - {_COLOR[self.winner]} wins."

    # -- simulated physical board ---------------------------------------------
    def legal_ucis(self) -> list[str]:
        if self.mode != "play" or self.staged:
            return []
        return [m.uci() for m in self.physical.legal_moves]

    def make_move(self, uci: str) -> None:
        """Simulate the player physically moving a piece (not yet confirmed)."""
        if self.mode != "play" or self.staged:
            return
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            self.report = "Bad move."
            return
        if move not in self.physical.legal_moves:
            self.report = "That is not a legal move on the board."
            return
        self.physical.push(move)
        self.staged = True
        self.report = f"{_COLOR[self.active].title()} moved - tap the clock to confirm."

    # -- the device buttons ----------------------------------------------------
    def confirm(self) -> None:
        """A clock tap: read the board through the camera + inference."""
        if self.mode != "play":
            return
        if not self.staged:
            self.report = "No move detected on the board."
            return
        frame = synth.render(board_to_grid(self.physical), SQUARE_PX,
                             seed=1000 + len(self.history), **RENDER_KW)
        grid, _conf = self.calib.classify(frame)
        self.detected = grid
        if self._is_end_gesture(grid):   # on hardware the camera sees this directly
            self._end_gesture(grid)
            return
        res = self.inf.observe(grid)
        if res.kind == "promotion":
            self.mode = "promotion"
            self.report = "Promotion - choose a piece on the clock."
        elif res.kind == "move":
            self._accept(res.san)
        else:  # error | ambiguous | incomplete | nochange
            self.report = f"Board unclear ({res.kind}): {res.note}. Please check."

    def promote(self, key: str) -> None:
        if self.mode != "promotion":
            return
        piece = _PROMO.get(key)
        if piece is None:
            return
        res = self.inf.resolve_promotion(piece)
        if res.kind != "move":
            self.report = res.note or "Promotion failed."
            return
        # Keep the simulated physical board consistent with the device's choice
        # (the disc looks identical, so this is retroactively correct).
        last = self.physical.pop()
        self.physical.push(chess.Move(last.from_square, last.to_square, promotion=piece))
        self.mode = "play"
        self._accept(res.san)

    # -- end-of-game gesture: both kings to the centre -------------------------
    def _is_end_gesture(self, grid) -> bool:
        """True when the occupancy shows both kings parked in the centre.

        Detected from occupancy alone using the believed king squares: both of
        them now read empty, and the centre carries at least one light and one
        dark piece. Both kings vacating at once cannot happen via a single legal
        ply, so this never fires during normal play.
        """
        board = self.inf.board
        wk, bk = board.king(chess.WHITE), board.king(chess.BLACK)
        if wk is None or bk is None:
            return False
        if grid[square_to_rc(wk)] != Cell.EMPTY or grid[square_to_rc(bk)] != Cell.EMPTY:
            return False
        lights = sum(grid[square_to_rc(sq)] == Cell.LIGHT for sq in _CENTER)
        darks = sum(grid[square_to_rc(sq)] == Cell.DARK for sq in _CENTER)
        return lights >= 1 and darks >= 1

    def _decode_result(self, grid) -> bool | None:
        """Read the result from the kings' squares: white king = the light piece,
        black king = the dark piece; both on light squares -> White, both on dark
        squares -> Black, mixed -> draw (None)."""
        lights = [sq for sq in _CENTER if grid[square_to_rc(sq)] == Cell.LIGHT]
        darks = [sq for sq in _CENTER if grid[square_to_rc(sq)] == Cell.DARK]
        if len(lights) == 1 and len(darks) == 1:
            wk_sq, bk_sq = lights[0], darks[0]
            on_light = _is_light_square(wk_sq), _is_light_square(bk_sq)
            if all(on_light):
                return chess.WHITE
            if not any(on_light):
                return chess.BLACK
        return None

    def _end_gesture(self, grid) -> None:
        winner = self._decode_result(grid)
        self.mode = "gameover"
        self.winner = winner
        self.staged = False
        if winner == chess.WHITE:
            self.result, who = "1-0", "White wins"
        elif winner == chess.BLACK:
            self.result, who = "0-1", "Black wins"
        else:
            self.result, who = "1/2-1/2", "Draw"
        self.report = f"{who} ({self.result}) - confirmed by kings to the centre."

    def end_by_kings_to_center(self, result: str = "draw") -> None:
        """Simulator hook: stage the kings-to-the-centre position for a given
        result and read it through the real vision path, exactly as the camera
        would. `result` is 'white' | 'black' | 'draw' and only chooses which
        squares the kings land on; the outcome is then *decoded* from the grid."""
        if self.mode == "gameover":
            return
        board = self.inf.board
        wk, bk = board.king(chess.WHITE), board.king(chess.BLACK)
        if wk is None or bk is None:
            return
        wt, bt = _RESULT_ARRANGEMENT.get(result, _RESULT_ARRANGEMENT["draw"])
        phys = chess.Board(None)
        # Clear the whole centre and keep everything else, then park the two kings
        # there - so the centre holds exactly the kings (what you'd do physically).
        for sq in chess.SQUARES:
            p = board.piece_at(sq)
            if p is not None and sq not in (wk, bk) and sq not in _CENTER:
                phys.set_piece_at(sq, p)
        phys.set_piece_at(wt, chess.Piece(chess.KING, chess.WHITE))
        phys.set_piece_at(bt, chess.Piece(chess.KING, chess.BLACK))
        self.physical = phys
        self.staged = False
        frame = synth.render(board_to_grid(phys), SQUARE_PX, seed=999, **RENDER_KW)
        grid, _conf = self.calib.classify(frame)
        self.detected = grid
        if self._is_end_gesture(grid):
            self._end_gesture(grid)
        else:
            self.report = "Kings-to-centre not recognised (board out of sync?)."

    def reset(self, chess960: bool = False, position: int | None = None) -> None:
        self.__init__(self.base, self.increment, chess960=chess960, position=position)

    def _accept(self, san: str) -> None:
        mover = self.active
        self.times[mover] += self.increment
        self.history.append(san)
        self.move_elapsed.append(round(self.elapsed, 1))
        self.positions.append(self.physical.board_fen())
        self.staged = False
        self.active = self.physical.turn
        self.report = f"{_COLOR[mover].title()}: {san}"
        if self.physical.is_game_over():
            self.mode = "gameover"
            outcome = self.physical.outcome()
            if outcome and outcome.winner is not None:
                self.winner = outcome.winner
                self.report = f"{san} - {_COLOR[self.winner]} wins ({outcome.termination.name.lower()})."
            else:
                self.report = f"{san} - draw ({outcome.termination.name.lower() if outcome else 'game over'})."

    # -- serialization for the web client -------------------------------------
    def snapshot(self) -> dict:
        promo = []
        if self.mode == "promotion":
            promo = ["q", "r", "b", "n"]
        return {
            "fen": self.physical.board_fen(),
            "active": _COLOR[self.active],
            "mode": self.mode,
            "staged": self.staged,
            "variant": self.variant,
            "position960": self.position960,
            "times": {"white": round(self.times[chess.WHITE], 1),
                      "black": round(self.times[chess.BLACK], 1)},
            "legal": self.legal_ucis(),
            "detected": [int(v) for v in self.detected.flatten()],
            "history": list(self.history),
            "move_elapsed": list(self.move_elapsed),
            "positions": list(self.positions),
            "start_wall": self.start_wall,
            "elapsed": round(self.elapsed, 1),
            "report": self.report,
            "winner": _COLOR[self.winner] if self.winner is not None else None,
            "result": self.result,
            "promo": promo,
        }


# ---------------------------------------------------------------------------
# Thin HTTP layer
# ---------------------------------------------------------------------------
_GAME = ClockGame()
_LOCK = threading.Lock()
_WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_PAGES = {
    "/": "index.html",        # landing page with links to the two devices
    "/board": "board.html",   # the board / camera screen
    "/clock": "clock.html",   # the clock device (the tablet)
    "/shared.js": "shared.js",
}
_CTYPE = {".html": "text/html; charset=utf-8",
          ".js": "application/javascript; charset=utf-8"}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence per-request logging
        pass

    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _serve_file(self, name):
        try:
            with open(os.path.join(_WEB_DIR, name), "rb") as f:
                body = f.read()
        except OSError:
            self.send_response(404)
            self.end_headers()
            return
        ext = os.path.splitext(name)[1]
        self.send_response(200)
        self.send_header("Content-Type", _CTYPE.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in _PAGES:
            self._serve_file(_PAGES[self.path])
        elif self.path == "/state":
            with _LOCK:
                _GAME.tick(time.time())
                self._send_json(_GAME.snapshot())
        else:
            self.send_response(204)
            self.end_headers()

    def do_POST(self):
        data = self._read_json()
        with _LOCK:
            _GAME.tick(time.time())
            if self.path == "/move":
                _GAME.make_move(data.get("uci", ""))
            elif self.path == "/confirm":
                _GAME.confirm()
            elif self.path == "/promote":
                _GAME.promote(data.get("piece", ""))
            elif self.path == "/gesture":
                _GAME.end_by_kings_to_center(data.get("result", "draw"))
            elif self.path == "/reset":
                v = data.get("variant")
                c960 = (_GAME.variant == "chess960") if v is None else (v == "chess960")
                _GAME.reset(chess960=c960, position=data.get("position"))
            else:
                self._send_json({"error": "unknown"}, 404)
                return
            self._send_json(_GAME.snapshot())


def run_server(host: str = "127.0.0.1", port: int = 8000,
               base_seconds: float = 300.0, increment: float = 3.0,
               start_fen: str | None = None, chess960: bool = False,
               position: int | None = None) -> None:
    global _GAME
    _GAME = ClockGame(base_seconds, increment, start_fen=start_fen,
                      chess960=chess960, position=position)
    httpd = ThreadingHTTPServer((host, port), _Handler)
    base = f"http://{host}:{port}"
    print(f"chessmon running at {base}/")
    print(f"  board screen : {base}/board")
    print(f"  clock device : {base}/clock")
    print("Open each on its own screen (same PC, or phone/iPad via the PC's LAN IP).")
    print("Press Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping.")
        httpd.shutdown()
