"""API REST (JSON) pour l'interface web."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from prolific.clients.ollama import OllamaClient
from prolific.config import Config
from prolific.db import Database, serialize_job
from prolific.orchestrator import Orchestrator
from prolific.rag import RagIndex
from prolific.traces import TraceLogger

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
    random_tags: bool = False
    random_tags_count: int = 15
    seed: int | None = None
    filename_prefix: str | None = None
    draft_batch_size: int | None = None
    enable_traces: bool = True


def build_api(
    *,
    config: Config,
    db: Database,
    ollama: OllamaClient,
    rag: RagIndex,
    manifest: dict[str, Any],
    orchestrator: Orchestrator,
    trace_logger: TraceLogger | None = None,
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
            random_tags=payload.random_tags,
            random_tags_count=payload.random_tags_count,
            filename_prefix=payload.filename_prefix,
            draft_batch_size=payload.draft_batch_size,
            enable_traces=payload.enable_traces,
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

    @router.delete("/jobs/{job_id}/traces")
    def delete_job_traces(job_id: int) -> dict[str, str]:
        """Retire uniquement les traces (dataset de fine-tuning), garde le job.

        Fonctionne même si le job a déjà été supprimé (les traces sont indépendantes).
        """
        if trace_logger is not None:
            trace_logger.delete_job(job_id)
        return {"status": "traces_deleted"}

    @router.delete("/jobs/{job_id}")
    def delete_job(job_id: int) -> dict[str, str]:
        """Supprime le job et ses images (fichiers si output_dir configuré).

        Les traces sont conservées : utiliser /jobs/{id}/traces pour les retirer,
        ou /jobs/{id}/purge pour tout supprimer.
        """
        if db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job introuvable")
        orchestrator.stop(job_id)
        if config.comfyui.output_dir:
            out = Path(config.comfyui.output_dir)
            for img in db.list_images(job_id):
                if img.get("path"):
                    try:
                        (out / img["path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
        db.delete_job(job_id)
        return {"status": "deleted"}

    @router.delete("/jobs/{job_id}/purge")
    def purge_job(job_id: int) -> dict[str, str]:
        """Supprime le job, ses images (fichiers) ET ses traces."""
        if db.get_job(job_id) is None:
            raise HTTPException(status_code=404, detail="job introuvable")
        orchestrator.stop(job_id)
        if config.comfyui.output_dir:
            out = Path(config.comfyui.output_dir)
            for img in db.list_images(job_id):
                if img.get("path"):
                    try:
                        (out / img["path"]).unlink(missing_ok=True)
                    except OSError:
                        pass
        db.delete_job(job_id)
        if trace_logger is not None:
            trace_logger.delete_job(job_id)
        return {"status": "purged"}

    return router
