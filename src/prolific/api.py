"""API REST (JSON) pour l'interface web."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from prolific.clients.ollama import OllamaClient
from prolific.config import Config
from prolific.db import Database, serialize_job
from prolific.orchestrator import Orchestrator
from prolific.rag import RagIndex

STYLES = ["tags", "prose", "hybrid"]
ORIENTATIONS = ["carre", "paysage", "portrait", "random", "auto"]


class JobCreate(BaseModel):
    theme: str
    workflow: str
    model: str | None = None
    objective_type: str = "count"
    n: int | None = None
    minutes: int | None = None
    orientation: str = "carre"
    style: str | None = None
    datasets: list[str] = []
    swap_vram: bool = False
    positive_suffix: str | None = None
    negative: str | None = None
    temperature: float | None = None
    batch: int = 1
    min_words: int = 0
    seed: int | None = None


def build_api(
    *,
    config: Config,
    db: Database,
    ollama: OllamaClient,
    rag: RagIndex,
    manifest: dict[str, Any],
    orchestrator: Orchestrator,
) -> APIRouter:
    router = APIRouter()

    def _image_url(path: str | None) -> str | None:
        """Construit l'URL ComfyUI /view d'une image (stockée en 'subfolder/filename')."""
        if not path:
            return None
        path = path.lstrip("/")
        if "/" in path:
            subfolder, filename = path.rsplit("/", 1)
        else:
            subfolder, filename = "", path
        base = config.comfyui.url.rstrip("/")
        return f"{base}/view?filename={filename}&subfolder={subfolder}&type=output"

    def _job_detail(job_id: int) -> dict[str, Any]:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job introuvable")
        job = serialize_job(job)
        images = db.list_images(job_id)
        for img in images:
            img["url"] = _image_url(img.get("path"))
        job["images"] = images
        return job

    @router.get("/config")
    def get_config() -> dict[str, Any]:
        models: list[str] = []
        try:
            models = [m["name"] for m in ollama.list_models()]
        except Exception:
            pass
        return {
            "workflows": [
                {
                    "name": name,
                    "description": entry.get("description", ""),
                    "style": entry.get("style", "tags"),
                    "formats": entry.get("formats", {}),
                }
                for name, entry in manifest.items()
            ],
            "models": models,
            "datasets": rag.list_datasets(),
            "styles": STYLES,
            "orientations": ORIENTATIONS,
        }

    @router.get("/model_temperature")
    def model_temperature(model: str) -> dict[str, float | None]:
        """Température déclarée par un modèle Ollama (None = défaut du modèle)."""
        return {"temperature": ollama.model_temperature(model)}

    @router.post("/jobs")
    def create_job(payload: JobCreate) -> dict[str, int]:
        if payload.objective_type == "count":
            if not payload.n:
                raise HTTPException(status_code=400, detail="n requis pour objective_type='count'")
            objective: dict = {"type": "count", "n": payload.n}
        elif payload.objective_type == "duration":
            if not payload.minutes:
                raise HTTPException(
                    status_code=400, detail="minutes requis pour objective_type='duration'"
                )
            objective = {"type": "duration", "minutes": payload.minutes}
        else:
            raise HTTPException(status_code=400, detail="objective_type invalide")

        job_id = orchestrator.submit(
            theme=payload.theme,
            workflow=payload.workflow,
            objective=objective,
            model=payload.model,
            style=payload.style,
            orientation=payload.orientation,
            datasets=payload.datasets,
            swap_vram=payload.swap_vram,
            seed=payload.seed,
            positive_suffix=payload.positive_suffix,
            negative=payload.negative,
            temperature=payload.temperature,
            batch=payload.batch,
            min_words=payload.min_words,
        )
        return {"job_id": job_id}

    @router.get("/jobs")
    def list_jobs() -> list[dict[str, Any]]:
        return [serialize_job(job) for job in db.list_jobs(100)]

    @router.get("/jobs/{job_id}")
    def get_job(job_id: int) -> dict[str, Any]:
        return _job_detail(job_id)

    @router.post("/jobs/{job_id}/stop")
    def stop_job(job_id: int) -> dict[str, str]:
        if db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job introuvable")
        orchestrator.stop(job_id)
        return {"status": "stopping"}

    return router
