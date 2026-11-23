"""Durable copies of the small, derived dashboard snapshots sent from the office."""

import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class SavedPublication:
    payload: str
    published_at: datetime | None


class PublicationOutbox:
    """Create an immutable draft per saved model and record its publish receipt."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        with closing(sqlite3.connect(path)) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS publications (
                    model_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    published_at TEXT
                )
            """)

    def get(self, key: str) -> SavedPublication | None:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT payload, published_at FROM publications WHERE model_key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        payload, published_at = row
        return SavedPublication(
            payload, datetime.fromisoformat(published_at) if published_at else None
        )

    def stage(self, key: str, payload: str) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "INSERT INTO publications (model_key, payload) VALUES (?, ?) "
                "ON CONFLICT (model_key) DO NOTHING",
                (key, payload),
            )

    def mark_published(self, key: str, published_at: datetime) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute(
                "UPDATE publications SET published_at = ? WHERE model_key = ? AND published_at IS NULL",
                (published_at.isoformat(), key),
            )
