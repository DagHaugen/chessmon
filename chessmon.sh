#!/bin/sh
# chessmon launcher (macOS/Linux). Uses the project venv once setup has created it, else python3.
#   ./chessmon.sh setup  |  ./chessmon.sh  |  ./chessmon.sh stop  |  ./chessmon.sh restart  |  ./chessmon.sh status
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PY="$DIR/.venv/bin/python"
[ -x "$PY" ] || PY=python3
exec "$PY" "$DIR/run.py" "$@"
