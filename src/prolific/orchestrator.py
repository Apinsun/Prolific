"""Orchestrateur : machine à états LangGraph pilotant un job de génération.

Graphe : plan → draft → generate → adapt → (draft | finish).

- `plan`     : dimensionne le lot (N images ou budget temps via calibration).
- `draft`    : rédige les prompts via le LLM (tool calling `search_terms`).
- `generate` : patch le workflow, soumet à ComfyUI, récupère les images
               (avec swap VRAM autour du lot si demandé).
- `adapt`    : met à jour la calibration, recalcule le lot suivant ou termine.
- `finish`   : clôture le job en base.

Checkpointing via SqliteSaver (reprise), arrêt via un Event consulté entre images.
Le graphe est synchrone et tourne dans un thread en arrière-plan.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from prolific.agent import draft_prompts_agent
from prolific.clients.comfyui import ComfyUIClient
from prolific.clients.ollama import OllamaClient
from prolific.config import Config
from prolific.db import Database
from prolific.prompting import PROMPTS_DIR, compose_positive, dedupe
from prolific.rag import RagIndex
from prolific.tools import make_search_terms_tool
from prolific.workflows import patch_workflow, resolve_format

logger = logging.getLogger(__name__)


class OrchestratorState(TypedDict, total=False):
    # configuration du job (posée au submit)
    job_id: int
    objective: dict            # {"type": "count", "n": N} | {"type": "duration", "minutes": M}
    theme: str
    workflow: str
    style: str                 # tags | prose | hybrid
    orientation: str           # carre | paysage | portrait
    datasets: list[str]
    swap_vram: bool
    model: str
    seed: int | None
    positive_suffix: str | None
    negative: str | None
    temperature: float | None

    # état d'exécution (construit au fil du graphe)
    batch_size: int
    prompts: list[str]
    generated: int
    target: int | None
    deadline: float | None     # timestamp monotonic de fin (mode durée)
    started_at: float
    batch_durations: list[float]
    continue_loop: bool
    status: str                # running | done | stopped | failed
    error: str | None
    stalled: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Orchestrator:
    """Construit et exécute le StateGraph pour un job donné."""

    PROBE_BATCH = 3            # lot de sonde quand aucune calibration
    DURATION_MARGIN = 30.0     # marge (s) réservée au swap final / fin de job
    MAX_HISTORY = 200          # nb max de prompts d'historique injectés au LLM

    def __init__(
        self,
        *,
        config: Config,
        db: Database,
        ollama: OllamaClient,
        comfyui: ComfyUIClient,
        rag: RagIndex,
        workflows_dir: Path,
        manifest: dict[str, Any],
        prompts_dir: Path = PROMPTS_DIR,
        llm_factory: Callable[[str, float], Any] | None = None,
        checkpointer_path: Path = Path("checkpoints.sqlite"),
    ) -> None:
        self.config = config
        self.db = db
        self.ollama = ollama
        self.comfyui = comfyui
        self.rag = rag
        self.workflows_dir = workflows_dir
        self.manifest = manifest
        self.prompts_dir = prompts_dir
        self._llm_factory = llm_factory or (
            lambda model, temperature: ChatOllama(
                model=model, base_url=self.config.llm.base_url, temperature=temperature
            )
        )

        self._stop_events: dict[int, threading.Event] = {}
        conn = sqlite3.connect(checkpointer_path, check_same_thread=False)
        self._checkpointer = SqliteSaver(conn)
        self._checkpointer.setup()
        self._graph = self._build_graph()

    # ── construction du graphe ────────────────────────────────────────
    def _build_graph(self):
        g = StateGraph(OrchestratorState)
        g.add_node("plan", self._plan)
        g.add_node("draft", self._draft)
        g.add_node("generate", self._generate)
        g.add_node("adapt", self._adapt)
        g.add_node("finish", self._finish)
        g.add_edge(START, "plan")
        g.add_edge("plan", "draft")
        g.add_edge("draft", "generate")
        g.add_edge("generate", "adapt")
        g.add_conditional_edges(
            "adapt",
            self._should_continue,
            {"draft": "draft", "finish": "finish"},
        )
        g.add_edge("finish", END)
        return g.compile(checkpointer=self._checkpointer)

    @staticmethod
    def _should_continue(state: OrchestratorState) -> str:
        return "draft" if state.get("continue_loop") else "finish"

    # ── nœuds ─────────────────────────────────────────────────────────
    def _plan(self, state: OrchestratorState) -> dict:
        objective = state["objective"]
        now = time.monotonic()

        if objective.get("type") == "count":
            n = int(objective["n"])
            return {
                "target": n,
                "batch_size": n,
                "deadline": None,
                "started_at": now,
                "generated": 0,
                "status": "running",
            }

        minutes = float(objective["minutes"])
        budget = max(0.0, minutes * 60 - self.DURATION_MARGIN)
        cal = self.db.get_calibration(state["workflow"])
        if cal and cal["avg_seconds"] > 0:
            batch = max(1, int(budget / cal["avg_seconds"]))
        else:
            batch = self.PROBE_BATCH
        return {
            "target": None,
            "batch_size": batch,
            "deadline": now + budget,
            "started_at": now,
            "generated": 0,
            "status": "running",
        }

    def _draft(self, state: OrchestratorState) -> dict:
        if state.get("error"):
            return {}
        batch_size = state.get("batch_size", 1)
        if batch_size <= 0:
            return {"prompts": [], "continue_loop": False}

        # le temps de rédaction compte dans le budget : on ne lance pas un lot
        # si la deadline est déjà dépassée.
        deadline = state.get("deadline")
        if deadline is not None and time.monotonic() >= deadline:
            return {"prompts": [], "continue_loop": False}

        history = [
            img["positive"] for img in self.db.list_images(state["job_id"])
        ][-self.MAX_HISTORY :]

        datasets = state.get("datasets", [])
        tools = [make_search_terms_tool(self.rag, datasets)] if datasets else []
        model = state.get("model") or self.config.llm.model
        temperature = state.get("temperature")
        if temperature is None:
            temperature = self.config.llm.temperature
        llm = self._llm_factory(model, temperature)
        logger.info(
            "job %s — rédaction de %d prompts (style=%s datasets=%s temperature=%s)",
            state["job_id"], batch_size, state.get("style"), datasets, temperature,
        )

        try:
            prompts = draft_prompts_agent(
                llm,
                tools,
                n=batch_size,
                theme=state["theme"],
                style=state.get("style", "tags"),
                datasets=datasets,
                history=history,
                prompts_dir=self.prompts_dir,
            )
        except Exception as exc:  # noqa: BLE001 — on capture tout pour tracer le job
            logger.error("job %s — échec de la rédaction : %s", state["job_id"], exc)
            return {
                "error": f"Échec de la rédaction : {exc}",
                "status": "failed",
                "continue_loop": False,
            }

        prompts = dedupe(prompts, history)
        logger.info(
            "job %s — %d prompts retenus après dédoublonnage", state["job_id"], len(prompts)
        )
        if not prompts and batch_size > 0:
            return {"prompts": [], "stalled": state.get("stalled", 0) + 1}
        return {"prompts": prompts, "stalled": 0}

    def _generate(self, state: OrchestratorState) -> dict:
        if state.get("error"):
            return {}

        job_id = state["job_id"]
        prompts = state.get("prompts", [])
        swap = state.get("swap_vram", False)
        model = state.get("model") or self.config.llm.model
        generated = state.get("generated", 0)
        target = state.get("target")
        deadline = state.get("deadline")

        # 1 cycle VRAM pour tout le lot : on décharge le LLM avant de générer
        if swap and model:
            self.ollama.unload(model)
        logger.info("job %s — génération de %d images (swap=%s)", job_id, len(prompts), swap)

        durations: list[float] = []
        try:
            for prompt in prompts:
                if self._stop_events.get(job_id, threading.Event()).is_set():
                    return {
                        "status": "stopped",
                        "continue_loop": False,
                        "prompts": [],
                        "generated": generated,
                        "batch_durations": durations,
                    }
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if target is not None and generated >= target:
                    break

                width, height = resolve_format(
                    self.manifest, state["workflow"], state.get("orientation", "carre")
                )
                positive = compose_positive(
                    prompt, state.get("positive_suffix"), state.get("style", "tags")
                )
                negative = state.get("negative")
                wf = patch_workflow(
                    self.manifest,
                    self.workflows_dir,
                    state["workflow"],
                    positive=positive,
                    negative=negative,
                    orientation=state.get("orientation", "carre"),
                    seed=state.get("seed"),
                    width=width,
                    height=height,
                )
                image_id = self.db.add_image(
                    job_id,
                    positive=positive,
                    negative=negative,
                    seed=state.get("seed"),
                    width=width,
                    height=height,
                )

                try:
                    prompt_id = self.comfyui.submit(wf)
                except Exception as exc:  # noqa: BLE001
                    self.db.update_image(image_id, status="failed", error=str(exc))
                    return {
                        "error": f"ComfyUI injoignable : {exc}",
                        "status": "failed",
                        "continue_loop": False,
                        "generated": generated,
                        "prompts": [],
                        "batch_durations": durations,
                    }

                t0 = time.monotonic()
                try:
                    history = self.comfyui.wait_for(
                        prompt_id, timeout=self.config.comfyui.timeout
                    )
                    images = self.comfyui.extract_images(history)
                    # préférer les images réellement sauvegardées (SaveImage),
                    # ignorer les aperçus (PreviewImage, type "temp")
                    saved = [i for i in images if i.get("type") == "output"] or images
                    duration = time.monotonic() - t0
                    path = (
                        f"{saved[0].get('subfolder', '')}/{saved[0].get('filename', '')}"
                        if saved
                        else None
                    )
                    self.db.update_image(
                        image_id, status="done", path=path, duration_seconds=duration
                    )
                    durations.append(duration)
                    generated += 1
                    self.db.update_job(job_id, images_done=generated)
                    logger.info("job %s — image %s ok (%.1fs)", job_id, image_id, duration)
                except Exception as exc:  # noqa: BLE001
                    self.db.update_image(image_id, status="failed", error=str(exc))
                    logger.warning("job %s — image %s échouée : %s", job_id, image_id, exc)
        finally:
            if swap:
                self.comfyui.free_vram()

        return {
            "generated": generated,
            "prompts": [],
            "batch_durations": durations,
            "continue_loop": True,
        }

    def _adapt(self, state: OrchestratorState) -> dict:
        if state.get("error") or state.get("status") == "stopped":
            return {"continue_loop": False}
        if state.get("stalled", 0) >= 2:
            # le LLM ne produit plus que des doublons : on arrête proprement
            return {"continue_loop": False, "status": "done"}

        for duration in state.get("batch_durations", []):
            self.db.update_calibration(state["workflow"], duration)

        generated = state.get("generated", 0)
        objective = state.get("objective", {})

        if objective.get("type") == "count":
            target = int(objective.get("n", 0))
            remaining = max(0, target - generated)
            if remaining > 0:
                return {"batch_size": remaining, "continue_loop": True}
            return {"continue_loop": False, "status": "done"}

        deadline = state.get("deadline")
        if deadline is None or time.monotonic() >= deadline:
            return {"continue_loop": False, "status": "done"}

        remaining = deadline - time.monotonic()
        cal = self.db.get_calibration(state["workflow"])
        if cal and cal["avg_seconds"] > 0:
            batch = max(1, int(remaining / cal["avg_seconds"]))
        else:
            batch = self.PROBE_BATCH
        return {"batch_size": batch, "continue_loop": True}

    def _finish(self, state: OrchestratorState) -> dict:
        status = state.get("status", "done")
        if state.get("error"):
            status = "failed"
        self.db.update_job(
            state["job_id"],
            status=status,
            finished_at=_now(),
            error=state.get("error"),
        )
        logger.info("job %s — terminé : %s", state["job_id"], status)
        return {"status": status}

    # ── exécution ─────────────────────────────────────────────────────
    def submit(
        self,
        *,
        theme: str,
        workflow: str,
        objective: dict,
        style: str | None = None,
        orientation: str = "carre",
        datasets: list[str] | None = None,
        swap_vram: bool = False,
        seed: int | None = None,
        model: str | None = None,
        positive_suffix: str | None = None,
        negative: str | None = None,
        temperature: float | None = None,
    ) -> int:
        """Crée un job en base et lance le graphe dans un thread. Retourne job_id."""
        entry = self.manifest.get(workflow, {})
        if style is None:
            style = entry.get("style", "tags")

        images_target = int(objective["n"]) if objective.get("type") == "count" else None
        job_id = self.db.create_job(
            objective=objective,
            theme=theme,
            workflow=workflow,
            style=style,
            orientation=orientation,
            datasets=datasets or [],
            swap_vram=swap_vram,
            seed=str(seed) if seed is not None else None,
            positive_suffix=positive_suffix,
            negative=negative,
            temperature=temperature,
            images_target=images_target,
        )
        self.db.update_job(job_id, status="running", started_at=_now())
        logger.info(
            "job %s soumis — workflow=%r thème=%r objectif=%r swap_vram=%s datasets=%s",
            job_id, workflow, theme, objective, swap_vram, datasets or [],
        )

        initial: OrchestratorState = {
            "job_id": job_id,
            "objective": objective,
            "theme": theme,
            "workflow": workflow,
            "style": style,
            "orientation": orientation,
            "datasets": datasets or [],
            "swap_vram": swap_vram,
            "model": model or self.config.llm.model,
            "seed": seed,
            "positive_suffix": positive_suffix,
            "negative": negative,
            "temperature": temperature,
        }

        self._stop_events[job_id] = threading.Event()
        thread = threading.Thread(target=self._run, args=(job_id, initial), daemon=True)
        thread.start()
        return job_id

    def _run(self, job_id: int, initial: OrchestratorState) -> None:
        run_config = {"configurable": {"thread_id": str(job_id)}}
        try:
            self._graph.invoke(initial, config=run_config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s — erreur non capturée", job_id)
            self.db.update_job(job_id, status="failed", error=str(exc), finished_at=_now())

    def stop(self, job_id: int) -> None:
        """Demande un arrêt propre (pris en compte entre deux images)."""
        event = self._stop_events.get(job_id)
        if event is not None:
            logger.info("job %s — demande d'arrêt", job_id)
            event.set()
