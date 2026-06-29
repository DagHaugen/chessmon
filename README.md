# chessmon

Camera-based chess **move monitor**. A camera mounted above the board reports
each move as it is played. It does **not** recognise pieces — it reads, per
square, only one of three states:

```
EMPTY   |   LIGHT (a white piece)   |   DARK (a black piece)
```

and infers the move from how that 8x8 grid changes between settled positions,
starting from the known standard setup.

chessmon is also a complete, self-hosted system you run for your club: phone & tablet
**chess clocks**, optional **camera move-detection**, **live broadcast** for spectators, and
**player + tournament** management. One person runs it on a laptop; everyone else just opens a
link. Works on **Windows, macOS and Linux**.

## Get started

### 1 · What you need
- **Python 3.10+** and **git** on the operator's computer (Windows, macOS or Linux).
- Players' **phones or tablets** as the clocks — any modern browser, nothing to install.
- The operator's machine needs **outbound** internet so phones can reach it through
  `comlos.com` (real HTTPS — no certificates, no firewall rules, no inbound ports).
- *(optional)* a **webcam** over each board for automatic move detection.

### 2 · Install
```bash
git clone https://github.com/DagHaugen/chessmon.git
cd chessmon
```
Create the environment and install dependencies (once):

| | command |
|--|--|
| **Windows** | `chessmon setup` |
| **macOS / Linux** | `./chessmon.sh setup` |

### 3 · Run it

| | command |
|--|--|
| **Windows** | `chessmon` |
| **macOS / Linux** | `./chessmon.sh` |

That starts the local server **and** the phone-bridge; press **Ctrl+C** to stop both. Now open
the operator console:

```
http://localhost:8000/app/admin.html
```

> **PowerShell users:** run the wrapper as `.\chessmon` (e.g. `.\chessmon setup`).

### 4 · Set things up (the console **Setup** page)
Open **Setup** and configure what you want — it's all optional except adding players:
- **Club / event name** — shown to spectators.
- **Get Stockfish** — engine for live suggested moves. Downloads the right build for your OS
  automatically (Windows, Intel **or** Apple-Silicon Mac, Linux).
- **Download FIDE list** — search players by surname / FIDE ID and autofill their ratings.
- **chessmon cloud** — broadcast games to online spectators (opt-in, involves an account).
- **Players** — add them, then assign two players + a time control to each table.

### 5 · Connect the clocks (and cameras)
Each table in the console shows a pairing **QR code**:
1. On the player's phone, **scan the console QR** → the clock opens (served from `comlos.com`,
   so screen/camera permissions just work — nothing to install).
2. *(optional camera)* on a second phone, **scan the clock's QR** → the camera page opens; point
   it at the board and capture the empty board + start position.

Names and the running clock now follow on the table's device, every move appears live in the
console, and — if you enabled it — spectators can watch online.

### Everyday commands
| command | what it does |
|---------|--------------|
| `chessmon` | run server + phone-bridge in this window (Ctrl+C stops both) |
| `chessmon start -d` | run in the background (logs to `chessmon.log`) |
| `chessmon stop` | stop a backgrounded instance |
| `chessmon restart` | stop, then start again |
| `chessmon status` | is the server up? port, bridge, Stockfish, cloud |
| `chessmon --no-bridge` | server only — local testing with no phones |
| `chessmon --port 9000` | run on a different port |

*(macOS / Linux: prefix each with `./chessmon.sh`; PowerShell: with `.\`.)*

---

The rest of this README explains **how the vision engine works** — the part that reads moves
off the board.

## Why three states, not two

Pure occupied/empty sensing cannot see a capture: the captured piece is replaced
on the *same* square, so the destination's occupancy never changes — you see a
piece vanish from its origin but have no signal for where it went. Adding a
single bit of colour per square (light-piece vs dark-piece, far easier than
identifying the piece) makes a capture observable as a **colour flip** at the
destination, and turns move inference into a clean, fully-determined problem.
See the design discussion that accompanied this build.

## Architecture

One strict boundary: **vision produces grids, inference produces moves.**

```
camera ─▶ calibrate_camera.warp ─▶ detector.classify ─▶ 8x8 grid ─▶ inference.observe ─▶ move
            (rectify to canonical)   (three-state)                    (state machine)
```

| file | role |
|------|------|
| `board_state.py` | `Cell` enum, the 3-state grid, grid↔python-chess mapping |
| `synth.py` | synthetic top-down renderer (noise/lighting/shadow/jitter) — lets the whole loop run with **no camera** |
| `geometry.py` | square → image ROI on the canonical board |
| `detector.py` | `Calibration` (learns thresholds from 2 reference frames), `classify` (frame → grid), `StabilityGate` (only classify settled, hand-free frames) |
| `inference.py` | `MoveInference` — matches each observation against every legal move's projected grid |
| `calibrate_camera.py` | webcam-only homography (rectify a real frame) |
| `sources.py` | webcam frame source |
| `app.py` | CLI: `selftest`, `render`, `webcam` |
| `tests/test_suite.py` | hardware-free verification of every design claim |

### How inference works

Rather than hand-coding move templates, `MoveInference` keeps a believed
`chess.Board`, and for each observed grid asks python-chess for every legal move,
projects the resulting position to a three-state grid, and keeps the move(s)
that reproduce the observation. This covers quiet moves, captures, castling, en
passant and promotion uniformly. **Legality is the safety net:** a vision error
almost always matches *no* legal move and is flagged instead of corrupting state.

Special handling:
- **Incomplete** — a frame that only removes pieces (a piece in hand) is held,
  not committed.
- **Promotion** — Q/R/B/N all project identically, so the move is reported with
  the type assumed Queen (the one thing colour-only sensing genuinely can't see).
- **Missed ply** — if no single move matches, it tries a unique two-half-move
  decomposition before giving up.

## Run the vision engine directly (no hardware)

```powershell
# from the repo root
.\.venv\Scripts\python.exe -m chessmon.app selftest      # plays a game through the full loop
.\.venv\Scripts\python.exe tests\test_suite.py           # full verification suite
.\.venv\Scripts\python.exe -m chessmon.app render "<FEN>" # save a synthetic frame to out/
```

`selftest` plays the Ruy Lopez Exchange (six captures + castling) by rendering
each position to a noisy, unevenly-lit, shadowed frame and recovering the move
from vision alone. The test suite additionally checks capture-at-destination, en
passant, promotion, the incomplete/transient guard, missed-ply recovery, illegal
-observation rejection, shadow rejection + low-contrast detection, and the
stability gate.

## Clock device (two-device web app)

A phone/iPad is the chess clock; tapping it is the move-commit signal that
triggers each camera read. Simulated on the PC as a local web app with **two
separate screens that share one game** (each later opens on its own device via
the PC's LAN IP):

```powershell
.\.venv\Scripts\python.exe -m chessmon.app clock                 # opens in your browser
.\.venv\Scripts\python.exe -m chessmon.app clock --fen "<FEN>"    # start from a position
python run_clock.py                                              # launcher; starts near a promotion
```

| URL | screen | role |
|-----|--------|------|
| `/` | landing | links to the two devices |
| `/board` | board / camera view | clickable board (stands in for the physical board + camera); make moves; signal end-of-game |
| `/clock` | clock device (the tablet) | two-sided clock; tap **✓** (confirmed) per move; pick the promotion piece |

Flow per move: make the move on `/board` → tap **✓** (confirmed) on the mover's side of
`/clock` → the move is read through the real detector + inference and the clock
switches. Both screens poll one shared backend, so they stay in lock-step. The
Black half of the clock is rotated 180° to face the player across the board.

**Move list / replay:** `/board` shows a scoresheet to the right — the game start
time, every move in algebraic notation, and the time since start when it was
played. Click any move to replay the board at that point (it outlines blue, with
a *Back to live* button to return to the running game). Backed by per-ply
`positions` (FENs) and `move_elapsed` in the snapshot — the same data a PGN export
will draw on.

**Variant — Standard or Chess960 (Fischer Random):** pick it with the toggle next
to *New game* on `/board` (Chess960 draws a random 0–959 position), or start the
server with `--chess960 [--position N]`. This needs almost nothing from the vision
side: a 960 start has the *same* occupancy/colour pattern (ranks 1–2 white, 7–8
black), so calibration is unchanged; `python-chess` supplies the 960-aware legal
moves and castling, and inference's projection-match recovers everything — pieces
from their shuffled squares, and 960 castling (king-onto-rook) reported as `O-O`.

**Promotion** is where the device earns its keep: on a pawn reaching the back
rank, the **✓** confirm button is replaced by piece buttons (**Queen dominant**, then Rook /
Bishop / Knight). Tapping one resolves the single thing colour-only vision cannot
see — the promoted piece type — via `MoveInference.resolve_promotion()`.

**End-of-game gesture (with result):** moving *both kings to the centre* ends the
game. It is not a chess rule, but two kings in the centre is an *illegal* position
that can never occur in play, so it is an unambiguous "we're done" signal the
camera recognises from occupancy alone (`ClockGame._is_end_gesture`). The kings'
placement also **encodes the result, read from the colour of the squares they
stand on**:

| both kings on… | centre squares | result |
|----------------|----------------|--------|
| light squares  | e4 + d5        | White wins (1-0) |
| dark squares   | d4 + e5        | Black wins (0-1) |
| mixed          | any other pair | draw (½-½) |

The detector identifies each king by piece colour (white king = light piece,
black king = dark piece) and reads the square colour underneath (`_decode_result`).
In the sim, the **White wins / Black wins / Draw** buttons on `/board` park the
kings on the matching squares, clear the centre, and read it back through the real
vision path; on hardware the players just place the kings (centre cleared) and the
camera decodes it — no button needed. The decoded result shows on both screens
(winner highlighted green) and as `1-0` / `0-1` / `½-½`.

Logic is covered headlessly by `tests\test_clock.py` (confirm/commit/switch,
capture, promotion picker incl. underpromotion, Fischer increment, flag-fall,
and the kings-to-centre gesture + its safety against false triggers).

## Requirements

Python 3.x, `numpy`, `python-chess`, `opencv-python` (see `requirements.txt`;
a `.venv` is already set up in this folder).

## License

chessmon is free software under the **GNU Affero General Public License v3.0** (AGPL‑3.0) — see
[`LICENSE`](LICENSE). You may run, study, modify and share it; if you offer a modified version as a
network service, AGPL requires that you also publish your changes.

Copyright © 2026 BONDATA AS.

The self‑hosted app is, and stays, free: it runs entirely on your own network — no account, nothing
leaves the LAN. The optional **chessmon cloud** broadcast service (live online spectators) is a separate,
hosted offering; using it is opt‑in and involves an account.
