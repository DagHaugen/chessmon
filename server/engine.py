"""Stockfish analysis for the suggested-moves overlay: best move + WDL (white-win / draw / black-win).

One reused engine process, guarded by a lock so the server can call `analyse` from a thread
(asyncio.to_thread) on each accepted move without races. Returns plain dicts, never raises into
the caller's hot path beyond the engine launch itself (callers should try/except)."""
import threading

import chess
import chess.engine

_engine = None
_path = None
_lock = threading.Lock()


def _get(path):
    global _engine, _path
    if _engine is None or _path != path:
        _close_locked()
        _engine = chess.engine.SimpleEngine.popen_uci(path)
        _path = path
        try:
            _engine.configure({"UCI_ShowWDL": True})
        except Exception:
            pass
    return _engine


def analyse(path, board, movetime=0.5, multipv=3):
    """Top-`multipv` suggestions for `board`. Returns, or None at game end:
        {"moves": [{san, uci, wdl:[win,draw,loss] from the MOVER's POV (permille), cp, mate}, ...],
         "wdl_white": [white_win, draw, black_win] permille}   # the best line, white POV -> the eval bar
    """
    if board.is_game_over():
        return None
    with _lock:
        infos = _get(path).analyse(board, chess.engine.Limit(time=movetime), multipv=multipv)
    if not isinstance(infos, list):
        infos = [infos]
    moves = []
    for info in infos:
        pv = info.get("pv") or []
        mv = pv[0] if pv else None
        if mv is None:
            continue
        wdl = info.get("wdl")
        wm = wdl.pov(board.turn) if wdl is not None else None   # the side-to-move's win/draw/loss
        score = info.get("score")
        cp = mate = None
        if score is not None:
            sm = score.pov(board.turn)
            if sm.is_mate():
                mate = sm.mate()
            else:
                cp = sm.score()
        moves.append({"san": board.san(mv), "uci": mv.uci(),
                      "wdl": [wm.wins, wm.draws, wm.losses] if wm is not None else None, "cp": cp, "mate": mate})
    bw = infos[0].get("wdl").white() if (infos and infos[0].get("wdl") is not None) else None
    return {"moves": moves, "wdl_white": [bw.wins, bw.draws, bw.losses] if bw is not None else None}


def _close_locked():
    global _engine
    if _engine is not None:
        try:
            _engine.quit()
        except Exception:
            pass
        _engine = None


def close():
    with _lock:
        _close_locked()
