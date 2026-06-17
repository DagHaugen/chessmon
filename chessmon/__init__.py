"""chessmon - camera-based chess move monitor (occupancy-only, three-state).

The package is split along one strict boundary:

    vision  ->  an 8x8 grid of {EMPTY, LIGHT, DARK}  ->  inference

`detector` produces grids from frames; `inference` turns a stream of grids
into chess moves. Everything else (synth, geometry, sources, calibrate_camera)
serves one of those two sides. The synthetic renderer lets the whole loop be
tested without any camera.
"""

__version__ = "0.1.0"
