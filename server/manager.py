"""Session registry. Indexes sessions by the table token (what the clock scans, QR-A) and
the pair token (what the camera scans off the clock, QR-B). In-memory for the MVP; swap for
Postgres-backed persistence later (see the ecosystem plan in memory)."""
from __future__ import annotations

import pickle
import secrets

from .game_session import Session


class SessionManager:
    def __init__(self):
        self._by_table: dict[str, Session] = {}
        self._by_pair: dict[str, Session] = {}

    def create_table(self, white="White", black="Black", variant="standard", name=""):
        token = secrets.token_urlsafe(8)
        s = Session(token, white, black, variant, name=name)
        self._by_table[token] = s
        self._by_pair[s.pair_token] = s
        return s

    def by_table(self, token):
        return self._by_table.get(token)

    def by_pair(self, token):
        return self._by_pair.get(token)

    def save(self, path):
        """Pickle the live sessions (calibrated boards + games + clocks) so a server restart
        resumes them — the clock/camera rejoin their tokens and find a calibrated session."""
        try:
            with open(path, "wb") as f:
                pickle.dump(self._by_table, f)
        except Exception:
            pass

    def load(self, path):
        try:
            with open(path, "rb") as f:
                self._by_table = pickle.load(f)
            self._by_pair = {s.pair_token: s for s in self._by_table.values()}
        except Exception:
            pass
