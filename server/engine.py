"""Stockfish analysis for the suggested-moves overlay.

Two modes share one reused engine process:
  - analyse(): a one-shot fixed-time read (kept for callers that just want a number).
  - start()/read()/stop(): a CONTINUOUS deepening analysis the suggestion scheduler drives --
    it reads the current best lines as the engine thinks deeper, and stops to switch boards.

Only ONE analysis runs at a time (the scheduler serialises), so start/read/stop are lock-free;
analyse() still takes the lock so a stray one-shot caller can't collide with the engine."""
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


def _format(infos, board):
    """A list of python-chess InfoDicts (one per PV) -> the suggest payload, or None if no PV yet.
        {moves:[{san,uci,wdl:[win,draw,loss] mover-POV,cp,mate}], wdl_white:[w,d,b], depth, fen}"""
    if not infos:
        return None
    moves = []
    for info in infos:
        pv = info.get("pv") or []
        mv = pv[0] if pv else None
        if mv is None:
            continue
        wdl = info.get("wdl")
        wm = wdl.pov(board.turn) if wdl is not None else None
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
    if not moves:
        return None
    bw = infos[0].get("wdl").white() if infos[0].get("wdl") is not None else None
    return {"moves": moves, "wdl_white": [bw.wins, bw.draws, bw.losses] if bw is not None else None,
            "depth": infos[0].get("depth"), "fen": board.fen()}


def analyse(path, board, movetime=0.5, multipv=3):
    """One-shot fixed-time read for `board`, or None at game end."""
    if board.is_game_over():
        return None
    with _lock:
        infos = _get(path).analyse(board, chess.engine.Limit(time=movetime), multipv=multipv)
    if not isinstance(infos, list):
        infos = [infos]
    return _format(infos, board)


def start(path, board, multipv=3):
    """Begin a CONTINUOUS deepening analysis (no time limit). Returns the handle for read()/stop()."""
    return _get(path).analysis(board, multipv=multipv)


def read(analysis, board):
    """Current best lines of a running analysis -> the suggest payload, or None if nothing yet."""
    try:
        return _format(list(analysis.multipv), board)
    except Exception:
        return None


def stop(analysis):
    try:
        analysis.stop()
        analysis.wait()          # let the engine actually halt before the next search starts
    except Exception:
        pass


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
