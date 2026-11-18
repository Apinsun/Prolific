"""Serveur MCP : expose Prolific aux agents LLM externes (pi, Claude, ...).

Transport : Streamable HTTP (monté sur le même process FastAPI, chemin `/mcp`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from prolific.clients.ollama import OllamaClient
from prolific.config import Config
from prolific.db import Database, serialize_job
from prolific.orchestrator import Orchestrator
from prolific.rag import RagIndex
from prolific.traces import TraceLogger

MCP_NAME = "prolific"


def build_mcp(
    *,
    orchestrator: Orchestrator,
    db: Database,
    rag: RagIndex,
    manifest: dict[str, Any],
    config: Config,
    ollama: OllamaClient,
    trace_logger: TraceLogger | None = None,
) -> FastMCP:
    """Construit le serveur MCP avec les outils v1."""
    mcp = FastMCP(
        MCP_NAME,
        instructions=(
            "Prolific orchestre la génération d'images en masse via ComfyUI. "
            "Utilisez create_job pour lancer une génération (N images ou T minutes), "
            "get_job/list_jobs pour suivre, stop_job pour arrêter, list_workflows pour "
            "lister les workflows, list_datasets/search_terms pour la recherche de termes."
        ),
    )

    @mcp.tool()
    def list_workflows() -> list[dict[str, Any]]:
        """Liste les workflows ComfyUI disponibles (nom, description, style, formats)."""
        return [
            {
                "name": name,
                "description": entry.get("description", ""),
                "style": entry.get("style", "tags"),
                "formats": entry.get("formats", {}),
            }
            for name, entry in manifest.items()
        ]

    @mcp.tool()
    def list_datasets() -> list[str]:
        """Liste les datasets RAG indexés (interrogeables via search_terms)."""
        return rag.list_datasets()

    @mcp.tool()
    def search_terms(
        concept: str,
        dataset: str | None = None,
        top_k: int = 10,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recherche des termes (tags ou vocabulaire) correspondant à un concept.

        Args:
            concept: Concept ou description en langage naturel.
            dataset: Nom d'un dataset précis (optionnel ; tous sinon).
            top_k: Nombre maximum de résultats.
            category: Catégorie optionnelle pour filtrer (voir list_categories).
        """
        datasets = [dataset] if dataset else None
        return rag.search(concept, datasets=datasets, top_k=top_k, category=category)

    @mcp.tool()
    def list_categories(dataset: str | None = None) -> list[str]:
        """Liste les catégories disponibles dans les datasets RAG."""
        datasets = [dataset] if dataset else None
        return rag.list_categories(datasets)

    @mcp.tool()
    def random_term(
        category: str | None = None,
        dataset: str | None = None,
        count: int = 3,
    ) -> list[str]:
        """Tire des termes aléatoires (dans une catégorie si précisée)."""
        datasets = [dataset] if dataset else None
        return rag.random_terms(datasets, k=count, category=category)

    @mcp.tool()
    def create_job(
        theme: str,
        workflow: str,
        objective_type: str = "count",
        n: int | None = None,
        minutes: int | None = None,
        orientation: str = "carre",
        style: str | None = None,
        datasets: list[str] | None = None,
        swap_vram: bool = False,
        seed: int | None = None,
        temperature: float | None = None,
        batch: int = 1,
        min_words: int = 0,
        random_tags: bool = False,
        positive_suffix: str | None = None,
        negative: str | None = None,
        filename_prefix: str | None = None,
    ) -> int:
        """Crée et lance un job de génération.

        Args:
            theme: Thème des images à générer.
            workflow: Nom du workflow ComfyUI (voir list_workflows).
            objective_type: 'count' (n images) ou 'duration' (minutes).
            n: Nombre d'images (si objective_type='count').
            minutes: Durée du job (si objective_type='duration').
            orientation: carre | paysage | portrait | random | auto (le modèle choisit).
            style: tags | prose | hybrid (défaut : celui du workflow).
            datasets: Datasets RAG actifs pour la rédaction.
            swap_vram: Décharger/recharger les modèles autour des lots.
            seed: Seed fixe (optionnel).
            temperature: Créativité du LLM (0.0-2.0 ; None = défaut du modèle).
            batch: Nombre d'images par prompt (seeds différents).
            min_words: Longueur minimale d'un prompt (0 = pas de minimum).
            random_tags: Injecter des termes aléatoires du RAG (si datasets sélectionnés).
            positive_suffix: Texte concaténé au prompt positif.
            negative: Prompt négatif (remplace celui du workflow).
            filename_prefix: Sous-dossier de sortie (relatif au output_dir ComfyUI).
        """
        if objective_type == "count":
            if not n:
                raise ValueError("n est requis pour objective_type='count'")
            objective: dict = {"type": "count", "n": int(n)}
        elif objective_type == "duration":
            if not minutes:
                raise ValueError("minutes est requis pour objective_type='duration'")
            objective = {"type": "duration", "minutes": int(minutes)}
        else:
            raise ValueError("objective_type doit être 'count' ou 'duration'")

        return orchestrator.submit(
            theme=theme,
            workflow=workflow,
            objective=objective,
            style=style,
            orientation=orientation,
            datasets=datasets,
            swap_vram=swap_vram,
            seed=seed,
            temperature=temperature,
            batch=batch,
            min_words=min_words,
            random_tags=random_tags,
            positive_suffix=positive_suffix,
            negative=negative,
            filename_prefix=filename_prefix,
        )

    @mcp.tool()
    def get_job(job_id: int) -> dict[str, Any]:
        """Récupère l'état d'un job et la liste de ses images."""
        job = db.get_job(job_id)
        if job is None:
            return {"error": f"job {job_id} introuvable"}
        job = serialize_job(job)
        job["images"] = db.list_images(job_id)
        return job

    @mcp.tool()
    def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
        """Liste les jobs récents (du plus récent au plus ancien)."""
        return [serialize_job(job) for job in db.list_jobs(limit)]

    @mcp.tool()
    def stop_job(job_id: int) -> str:
        """Demande l'arrêt propre d'un job (pris en compte entre deux images)."""
        orchestrator.stop(job_id)
        return f"Arrêt demandé pour le job {job_id}."

    @mcp.tool()
    def delete_traces(job_id: int) -> str:
        """Retire un job des traces (dataset de fine-tuning), sans supprimer le job."""
        if trace_logger is not None:
            trace_logger.delete_job(job_id)
        return f"Traces du job {job_id} retirées."

    @mcp.tool()
    def delete_job(job_id: int) -> str:
        """Supprime un job, ses images et ses traces (fichiers si output_dir configuré)."""
        if db.get_job(job_id) is None:
            return f"Job {job_id} introuvable."
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
        return f"Job {job_id} supprimé."

    @mcp.tool()
    def model_temperature(model: str) -> float | None:
        """Température déclarée par un modèle Ollama (None = défaut du modèle)."""
        return ollama.model_temperature(model)

    return mcp
