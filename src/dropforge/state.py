from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

from .models import Observation


class StateStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS target_state (
                target_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                digest TEXT NOT NULL,
                checked_at REAL NOT NULL,
                payload TEXT NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS target_control (
                target_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
                updated_at REAL NOT NULL
            )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_at REAL NOT NULL,
                payload TEXT NOT NULL
            )"""
        )
        self.connection.commit()
        self.lock = threading.Lock()

    def record(self, observation: Observation) -> bool:
        payload = json.dumps(observation.public_dict(), sort_keys=True)
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT digest FROM target_state WHERE target_id = ?", (observation.target_id,)
            ).fetchone()
            changed = row is None or row[0] != observation.digest
            self.connection.execute(
                """INSERT INTO target_state(target_id, status, digest, checked_at, payload)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(target_id) DO UPDATE SET
                     status=excluded.status, digest=excluded.digest,
                     checked_at=excluded.checked_at, payload=excluded.payload""",
                (observation.target_id, observation.status, observation.digest, observation.checked_at, payload),
            )
            if changed:
                self.connection.execute(
                    "INSERT INTO events(target_id, status, checked_at, payload) VALUES (?, ?, ?, ?)",
                    (observation.target_id, observation.status, observation.checked_at, payload),
                )
            return changed

    def events(self, limit: int = 50) -> list[dict]:
        if limit < 1 or limit > 1000:
            raise ValueError("event limit must be between 1 and 1000")
        with self.lock:
            rows = self.connection.execute(
                "SELECT payload FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def set_target_enabled(self, target_id: str, enabled: bool) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO target_control(target_id, enabled, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(target_id) DO UPDATE SET
                     enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (target_id, int(enabled), time.time()),
            )

    def target_enabled(self, target_id: str, *, default: bool) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT enabled FROM target_control WHERE target_id = ?", (target_id,)
            ).fetchone()
        return default if row is None else bool(row[0])

    def target_statuses(self, defaults: dict[str, bool]) -> list[dict]:
        with self.lock:
            controls = dict(self.connection.execute(
                "SELECT target_id, enabled FROM target_control"
            ).fetchall())
            rows = dict(self.connection.execute(
                "SELECT target_id, payload FROM target_state"
            ).fetchall())
        result = []
        for target_id, default in defaults.items():
            item = {
                "target_id": target_id,
                "enabled": bool(controls.get(target_id, default)),
                "observation": json.loads(rows[target_id]) if target_id in rows else None,
            }
            result.append(item)
        return result

    def close(self) -> None:
        self.connection.close()
