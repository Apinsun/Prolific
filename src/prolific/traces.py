"""Capture de traces JSONL (pour un éventuel fine-tuning ultérieur).

Écrit dans `traces/` (gitignoré) :
- `drafts.jsonl`  : consigne → prompts générés (+ orientations, datasets, température).
- `images.jsonl`  : prompt final → résultat de génération (seed, dimensions, statut).
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class TraceLogger:
    def __init__(self, directory: Path, enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.enabled = enabled
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _append(self, filename: str, record: dict[str, Any]) -> None:
        if not self.enabled:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with (self.directory / filename).open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_draft(
        self,
        *,
        job_id: int,
        theme: str,
        style: str | None,
        datasets: list[str],
        n: int,
        temperature: float | None,
        prompts: list[str],
        orientations: list[str | None],
        tool_calls: list[dict],
        conversation: list[dict],
        model: str | None = None,
        orientation: str | None = None,
    ) -> None:
        self._append(
            "drafts.jsonl",
            {
                "timestamp": self._now(),
                "job_id": job_id,
                "theme": theme,
                "style": style,
                "datasets": datasets,
                "n": n,
                "temperature": temperature,
                "model": model,
                "orientation": orientation,
                "prompts": prompts,
                "orientations": orientations,
                "tool_calls": tool_calls,
                "conversation": conversation,
            },
        )

    def log_image(
        self,
        *,
        job_id: int,
        image_id: int,
        positive: str,
        negative: str | None,
        seed: int,
        width: int,
        height: int,
        status: str,
        path: str | None = None,
        duration: float | None = None,
        error: str | None = None,
    ) -> None:
        self._append(
            "images.jsonl",
            {
                "timestamp": self._now(),
                "job_id": job_id,
                "image_id": image_id,
                "positive": positive,
                "negative": negative,
                "seed": seed,
                "width": width,
                "height": height,
                "status": status,
                "path": path,
                "duration_seconds": duration,
                "error": error,
            },
        )

    def delete_job(self, job_id: int) -> None:
        """Supprime les traces d'un job (réécrit les fichiers sans ses lignes)."""
        for filename in ("drafts.jsonl", "images.jsonl"):
            path = self.directory / filename
            if not path.exists():
                continue
            kept: list[str] = []
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("job_id") == job_id:
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
                kept.append(line)
            path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
