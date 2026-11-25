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
        self.test_directory = self.directory / "test"
        self.enabled = enabled
        self._lock = threading.Lock()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _append(self, filename: str, record: dict[str, Any], test_mode: bool = False) -> None:
        if not self.enabled:
            return
        base = self.test_directory if test_mode else self.directory
        base.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with (base / filename).open("a", encoding="utf-8") as f:
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
        test_mode: bool = False,
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
            test_mode=test_mode,
        )

    def log_image(
        self,
        *,
        job_id: int,
        image_id: int,
        positive: str,
        drafted: str | None = None,
        negative: str | None,
        seed: int,
        width: int,
        height: int,
        status: str,
        path: str | None = None,
        duration: float | None = None,
        error: str | None = None,
        test_mode: bool = False,
    ) -> None:
        self._append(
            "images.jsonl",
            {
                "timestamp": self._now(),
                "job_id": job_id,
                "image_id": image_id,
                "positive": positive,
                "drafted": drafted,
                "negative": negative,
                "seed": seed,
                "width": width,
                "height": height,
                "status": status,
                "path": path,
                "duration_seconds": duration,
                "error": error,
            },
            test_mode=test_mode,
        )

    def log_metric(self, record: dict[str, Any]) -> None:
        """Enregistre une métrique d'évaluation (mode test) dans traces/test/metrics.jsonl."""
        self._append("metrics.jsonl", record, test_mode=True)

    def delete_job(self, job_id: int) -> None:
        """Supprime les traces d'un job (réécrit les fichiers sans ses lignes)."""
        for base in (self.directory, self.test_directory):
            for filename in ("drafts.jsonl", "images.jsonl", "metrics.jsonl"):
                self._drop_job(base / filename, job_id)

    def _drop_job(self, path: Path, job_id: int) -> None:
        if not path.exists():
            return
        kept: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(line).get("job_id") == job_id:
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
            kept.append(line)
        path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
