"""Persistance SQLite : jobs, images, calibration de durée."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DB_PATH = Path("prolific.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    status        TEXT NOT NULL DEFAULT 'pending',
    objective     TEXT NOT NULL,
    theme         TEXT NOT NULL,
    workflow      TEXT NOT NULL,
    style         TEXT,
    orientation   TEXT NOT NULL DEFAULT 'carre',
    datasets      TEXT NOT NULL DEFAULT '[]',
    swap_vram         INTEGER NOT NULL DEFAULT 0,
    seed              TEXT,
    positive_suffix   TEXT,
    negative          TEXT,
    temperature       REAL,
    batch             INTEGER NOT NULL DEFAULT 1,
    min_words         INTEGER NOT NULL DEFAULT 0,
    images_target     INTEGER,
    images_done   INTEGER NOT NULL DEFAULT 0,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    started_at    TEXT,
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS images (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER NOT NULL REFERENCES jobs(id),
    positive         TEXT NOT NULL,
    negative         TEXT,
    seed             INTEGER,
    width            INTEGER,
    height           INTEGER,
    status           TEXT NOT NULL DEFAULT 'pending',
    path             TEXT,
    error            TEXT,
    duration_seconds REAL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calibration (
    workflow    TEXT PRIMARY KEY,
    avg_seconds REAL NOT NULL,
    samples     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_job(row: dict[str, Any]) -> dict[str, Any]:
    """Décode les colonnes stockées en JSON texte (objective, datasets) en objets."""
    out = dict(row)
    for field in ("objective", "datasets"):
        if field in out and isinstance(out[field], str):
            try:
                out[field] = json.loads(out[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return out


# colonnes ajoutées après coup : migration légère pour les bases existantes
_COLUMN_MIGRATIONS = [
    ("jobs", "positive_suffix", "TEXT"),
    ("jobs", "negative", "TEXT"),
    ("jobs", "temperature", "REAL"),
    ("jobs", "batch", "INTEGER NOT NULL DEFAULT 1"),
    ("jobs", "min_words", "INTEGER NOT NULL DEFAULT 0"),
]


class Database:
    """Petit wrapper sqlite3 : une connexion par opération, verrou global."""

    def __init__(self, path: Path = DB_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            self._migrate(conn)
            conn.commit()
        finally:
            conn.close()

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Ajoute les colonnes manquantes sur une base existante."""
        for table, column, ctype in _COLUMN_MIGRATIONS:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── helpers bas niveau ──────────────────────────────────────────
    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(sql, params)
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    def _read_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(sql, params).fetchone()
                return dict(row) if row else None
            finally:
                conn.close()

    def _read_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            conn = self._connect()
            try:
                return [dict(r) for r in conn.execute(sql, params).fetchall()]
            finally:
                conn.close()

    # ── jobs ────────────────────────────────────────────────────────
    def create_job(
        self,
        *,
        objective: dict,
        theme: str,
        workflow: str,
        style: str | None = None,
        orientation: str = "carre",
        datasets: list[str] | None = None,
        swap_vram: bool = False,
        seed: str | None = None,
        positive_suffix: str | None = None,
        negative: str | None = None,
        temperature: float | None = None,
        batch: int = 1,
        min_words: int = 0,
        images_target: int | None = None,
    ) -> int:
        now = _now()
        return self._write(
            """INSERT INTO jobs
               (objective, theme, workflow, style, orientation, datasets,
                swap_vram, seed, positive_suffix, negative, temperature, batch, min_words,
                images_target, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                json.dumps(objective),
                theme,
                workflow,
                style,
                orientation,
                json.dumps(datasets or []),
                int(swap_vram),
                seed,
                positive_suffix,
                negative,
                temperature,
                batch,
                min_words,
                images_target,
                now,
                now,
            ),
        )

    def get_job(self, job_id: int) -> dict[str, Any] | None:
        return self._read_one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._read_all("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))

    def mark_interrupted(self) -> int:
        """Marque les jobs restés 'running'/'pending' (orphelins) comme interrompus.

        À appeler au démarrage du service : un job dont le thread est mort après un
        arrêt du process ne doit pas rester 'running' indéfiniment.
        """
        now = _now()
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "UPDATE jobs SET status = 'stopped', "
                    "error = 'interrompu (redémarrage du service)', "
                    "finished_at = ?, updated_at = ? WHERE status IN ('running', 'pending')",
                    (now, now),
                )
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()

    def update_job(
        self,
        job_id: int,
        *,
        status: str | None = None,
        error: str | None = None,
        images_done: int | None = None,
        images_target: int | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if images_done is not None:
            sets.append("images_done = ?")
            params.append(images_done)
        if images_target is not None:
            sets.append("images_target = ?")
            params.append(images_target)
        if started_at is not None:
            sets.append("started_at = ?")
            params.append(started_at)
        if finished_at is not None:
            sets.append("finished_at = ?")
            params.append(finished_at)
        sets.append("updated_at = ?")
        params.append(_now())
        params.append(job_id)
        self._write(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", tuple(params))

    # ── images ──────────────────────────────────────────────────────
    def add_image(
        self,
        job_id: int,
        *,
        positive: str,
        negative: str | None,
        seed: int | None,
        width: int,
        height: int,
    ) -> int:
        return self._write(
            """INSERT INTO images (job_id, positive, negative, seed, width, height, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (job_id, positive, negative, seed, width, height, _now()),
        )

    def update_image(
        self,
        image_id: int,
        *,
        status: str | None = None,
        path: str | None = None,
        error: str | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if path is not None:
            sets.append("path = ?")
            params.append(path)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if duration_seconds is not None:
            sets.append("duration_seconds = ?")
            params.append(duration_seconds)
        params.append(image_id)
        self._write(f"UPDATE images SET {', '.join(sets)} WHERE id = ?", tuple(params))

    def list_images(self, job_id: int) -> list[dict[str, Any]]:
        return self._read_all("SELECT * FROM images WHERE job_id = ? ORDER BY id", (job_id,))

    # ── calibration ─────────────────────────────────────────────────
    def get_calibration(self, workflow: str) -> dict[str, Any] | None:
        return self._read_one(
            "SELECT avg_seconds, samples FROM calibration WHERE workflow = ?", (workflow,)
        )

    def update_calibration(self, workflow: str, duration_seconds: float) -> None:
        now = _now()
        row = self.get_calibration(workflow)
        if row is None:
            self._write(
                "INSERT INTO calibration (workflow, avg_seconds, samples, updated_at) "
                "VALUES (?, ?, 1, ?)",
                (workflow, duration_seconds, now),
            )
        else:
            samples = row["samples"] + 1
            avg = (row["avg_seconds"] * row["samples"] + duration_seconds) / samples
            self._write(
                "UPDATE calibration SET avg_seconds = ?, samples = ?, "
                "updated_at = ? WHERE workflow = ?",
                (avg, samples, now, workflow),
            )
