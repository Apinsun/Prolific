"""Orchestrateur : machine à états LangGraph pilotant un job de génération.

Graphe : plan → draft → generate → adapt → (draft | finish).

- `plan`     : dimensionne le lot (N images ou budget temps via calibration).
- `draft`    : rédige les prompts via le LLM (tool calling `search_terms`).
- `generate` : patch le workflow, soumet à ComfyUI, récupère les images
               (avec swap VRAM autour du lot si demandé).
- `adapt`    : met à jour la calibration, recalcule le lot suivant ou termine.
- `finish`   : clôture le job en base.

Checkpointing via SqliteSaver (reprise), arrêt via un Event consulté entre images.
Le graphe est synchrone ; un worker unique dépile les jobs (FIFO) en arrière-plan.
"""

from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypedDict

from langchain_ollama import ChatOllama
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from prolific.agent import draft_prompts_agent
from prolific.clients.comfyui import ComfyUIClient
from prolific.clients.ollama import OllamaClient
from prolific.config import Config
from prolific.db import Database
from prolific.prompting import PROMPTS_DIR, compose_positive
from prolific.rag import RagIndex
from prolific.tools import (
    make_list_categories_tool,
    make_random_term_tool,
    make_search_terms_tool,
)
from prolific.traces import TraceLogger
from prolific.workflows import list_orientations, patch_workflow, resolve_format

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
    positive_prefix: str | None
    negative: str | None
    temperature: float | None
    batch: int
    min_words: int
    random_tags: bool
    random_tags_count: int
    filename_prefix: str | None
    draft_batch_size: int | None
    enable_traces: bool
    preset: str | None
    forced_searches: int
    test_mode: bool
    random_datasets: list[str] | None

    # état d'exécution (construit au fil du graphe)
    batch_size: int
    prompts: list[str]
    orientations: list[str | None]
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
        trace_logger: TraceLogger | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.ollama = ollama
        self.comfyui = comfyui
        self.rag = rag
        self.workflows_dir = workflows_dir
        self.manifest = manifest
        self.prompts_dir = prompts_dir
        self.trace_logger = trace_logger
        self._llm_factory = llm_factory or (
            lambda model, temperature: ChatOllama(
                model=model,
                base_url=self.config.llm.base_url,
                temperature=temperature,
                reasoning=True,  # capture le raisonnement dans additional_kwargs
            )
        )

        self._stop_events: dict[int, threading.Event] = {}
        self._queue: deque[tuple[int, OrchestratorState | None]] = deque()
        self._global_pause = threading.Event()
        self._current_job_id: int | None = None
        self._enqueued: set[int] = set()
        conn = sqlite3.connect(checkpointer_path, check_same_thread=False)
        self._checkpointer = SqliteSaver(conn)
        self._checkpointer.setup()
        self._graph = self._build_graph()
        self._recover_pending_jobs()
        self._worker = threading.Thread(
            target=self._worker_loop, daemon=True, name="job-worker"
        )
        self._worker.start()

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
        if not self.comfyui.is_online():
            return {
                "error": f"ComfyUI injoignable ({self.comfyui.url})",
                "status": "failed",
                "continue_loop": False,
            }
        objective = state["objective"]
        now = time.monotonic()

        images_per_prompt = max(1, int(state.get("batch", 1)))

        if objective.get("type") == "count":
            n = int(objective["n"])
            cap = state.get("draft_batch_size")
            batch_size = min(n, cap) if cap else n
            return {
                "target": n * images_per_prompt,
                "batch_size": batch_size,
                "deadline": None,
                "started_at": now,
                "generated": 0,
                "status": "running",
            }

        minutes = float(objective["minutes"])
        budget = max(0.0, minutes * 60 - self.DURATION_MARGIN)
        cal = self.db.get_calibration(state["workflow"])
        if cal and cal["avg_seconds"] > 0:
            per_prompt = cal["avg_seconds"] * images_per_prompt
            batch = max(1, int(budget / per_prompt))
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
        trace = self.trace_logger is not None and (
            state.get("enable_traces", True) or state.get("test_mode", False)
        )
        chunk_size = state.get("batch_size", 1)
        if chunk_size <= 0:
            return {"prompts": [], "continue_loop": False}

        # le temps de rédaction compte dans le budget : on ne lance pas un lot
        # si la deadline est déjà dépassée.
        deadline = state.get("deadline")
        if deadline is not None and time.monotonic() >= deadline:
            return {"prompts": [], "continue_loop": False}

        job_id = state["job_id"]
        datasets = state.get("datasets", [])
        tools = []
        if datasets:
            tools = [
                make_search_terms_tool(self.rag, datasets),
                make_list_categories_tool(self.rag, datasets),
                make_random_term_tool(self.rag, datasets),
            ]
        random_datasets = state.get("random_datasets") or datasets
        model = state.get("model") or self.config.llm.model
        temperature = state.get("temperature")  # None = défaut du modèle Ollama
        llm = self._llm_factory(model, temperature)

        # Combien de prompts rédiger dans cette passe ?
        #  - mode count : tout ce qui reste → on pré-rédige l'intégralité du job,
        #    puis _generate enchaîne toutes les images en un seul cycle VRAM.
        #  - mode durée : un seul chunk (adaptatif, le budget dépend de la vitesse).
        objective = state.get("objective", {})
        images_per_prompt = max(1, int(state.get("batch", 1)))
        if objective.get("type") == "count":
            target = state.get("target") or 0
            remaining_images = max(0, target - state.get("generated", 0))
            total_needed = math.ceil(remaining_images / images_per_prompt)
        else:
            total_needed = chunk_size

        history = [
            img["positive"] for img in self.db.list_images(job_id)
        ][-self.MAX_HISTORY :]

        all_prompts: list[str] = []
        all_orientations: list[str | None] = []
        stalled = state.get("stalled", 0)

        while len(all_prompts) < total_needed:
            n_chunk = min(chunk_size, total_needed - len(all_prompts))
            if n_chunk <= 0:
                break

            random_terms: list[list[str]] = []
            if state.get("random_tags") and random_datasets:
                k = state.get("random_tags_count") or 15
                random_terms = [
                    self.rag.random_terms(random_datasets, k=k)
                    for _ in range(n_chunk)
                ]
            logger.info(
                "job %s — rédaction de %d prompts (style=%s datasets=%s temp=%s random=%d)",
                job_id, n_chunk, state.get("style"), datasets, temperature,
                len(random_terms),
            )

            try:
                prompts, orientations, tool_calls, conversation = draft_prompts_agent(
                    llm,
                    tools,
                    n=n_chunk,
                    theme=state["theme"],
                    style=state.get("style", "tags"),
                    datasets=datasets,
                    history=history,
                    min_words=state.get("min_words", 0),
                    random_terms=random_terms,
                    prompts_dir=self.prompts_dir,
                    preset=state.get("preset"),
                    forced_searches=state.get("forced_searches", 0),
                )
            except Exception as exc:  # noqa: BLE001
                if all_prompts:
                    logger.warning(
                        "job %s — échec rédaction d'un chunk (on garde %d prompts) : %s",
                        job_id, len(all_prompts), exc,
                    )
                    break
                logger.error("job %s — échec de la rédaction : %s", job_id, exc)
                return {
                    "error": f"Échec de la rédaction : {exc}",
                    "status": "failed",
                    "continue_loop": False,
                }

            # dédoublonnage en préservant l'alignement prompts/orientations
            seen = set(history)
            kept_prompts: list[str] = []
            kept_orientations: list[str | None] = []
            for p, o in zip(prompts, orientations):
                if p in seen:
                    continue
                seen.add(p)
                kept_prompts.append(p)
                kept_orientations.append(o)
            prompts = kept_prompts
            orientations = kept_orientations

            logger.info(
                "job %s — %d prompts retenus après dédoublonnage", job_id, len(prompts)
            )
            for i, (p, o) in enumerate(zip(prompts, orientations), 1):
                logger.info(
                    "job %s — prompt %d [%s] : %s", job_id, i, o or "?", p
                )
            if trace:
                self.trace_logger.log_draft(
                    job_id=job_id,
                    theme=state["theme"],
                    style=state.get("style"),
                    datasets=datasets,
                    n=n_chunk,
                    temperature=temperature,
                    model=model,
                    orientation=state.get("orientation"),
                    prompts=prompts,
                    orientations=orientations,
                    tool_calls=tool_calls,
                    conversation=conversation,
                    test_mode=state.get("test_mode", False),
                )
            if trace and state.get("test_mode"):
                errors = [
                    {"name": tc.get("name"), "args": tc.get("args"), "error": tc.get("result")}
                    for tc in tool_calls
                    if tc.get("result", "").startswith(("Erreur de l'outil", "Outil inconnu"))
                ]
                self.trace_logger.log_metric({
                    "timestamp": _now(),
                    "job_id": job_id,
                    "theme": state["theme"],
                    "style": state.get("style"),
                    "model": model,
                    "n_requested": n_chunk,
                    "n_returned": len(prompts),
                    "tool_call_count": len(tool_calls),
                    "tool_call_errors": errors,
                })

            if not prompts:
                stalled += 1
                break

            all_prompts.extend(prompts)
            all_orientations.extend(orientations)
            history = (history + prompts)[-self.MAX_HISTORY :]
            stalled = 0

        if not all_prompts and total_needed > 0:
            return {
                "prompts": [],
                "orientations": [],
                "stalled": stalled,
            }
        return {
            "prompts": all_prompts,
            "orientations": all_orientations,
            "stalled": stalled,
        }

    def _generate(self, state: OrchestratorState) -> dict:
        if state.get("error"):
            return {}

        trace = self.trace_logger is not None and (
            state.get("enable_traces", True) or state.get("test_mode", False)
        )
        job_id = state["job_id"]
        prompts = state.get("prompts", [])
        orientations = state.get("orientations", [])
        swap = state.get("swap_vram", False)
        model = state.get("model") or self.config.llm.model
        generated = state.get("generated", 0)
        target = state.get("target")
        deadline = state.get("deadline")
        images_per_prompt = max(1, int(state.get("batch", 1)))

        # idempotence (reprise après pause) : la base fait foi sur l'avancement réel
        db_done = self.db.get_job(job_id)["images_done"] or 0
        done_in_batch = max(0, db_done - generated)
        generated = db_done
        start_idx = done_in_batch // images_per_prompt

        # 1 cycle VRAM pour tout le lot : on décharge le LLM avant de générer
        if swap and model:
            self.ollama.unload(model)
        logger.info(
            "job %s — génération de %d prompts × %d image(s) (swap=%s)",
            job_id, len(prompts), images_per_prompt, swap,
        )

        durations: list[float] = []
        try:
            for idx in range(start_idx, len(prompts)):
                prompt = prompts[idx]
                prompt_orientation = orientations[idx] if idx < len(orientations) else None
                if state.get("orientation") == "auto":
                    valid = list_orientations(self.manifest, state["workflow"])
                    orientation = (
                        prompt_orientation if prompt_orientation in valid
                        else (valid[0] if valid else "carre")
                    )
                else:
                    orientation = state.get("orientation", "carre")
                width, height = resolve_format(
                    self.manifest, state["workflow"], orientation
                )
                positive = compose_positive(
                    prompt,
                    state.get("positive_suffix"),
                    state.get("style", "tags"),
                    prefix=state.get("positive_prefix"),
                )
                negative = state.get("negative")

                if self._stop_events.get(job_id, threading.Event()).is_set():
                    interrupt("pause demandée")
                if deadline is not None and time.monotonic() >= deadline:
                    break
                if target is not None and generated >= target:
                    break

                # seed unique pour le lot (vrai batching : un envoi = N images)
                if state.get("seed") is not None:
                    base_seed = state["seed"]
                else:
                    base_seed = random.randint(0, 2**63 - 1)

                wf = patch_workflow(
                    self.manifest,
                    self.workflows_dir,
                    state["workflow"],
                    positive=positive,
                    negative=negative,
                    orientation=state.get("orientation", "carre"),
                    seed=base_seed,
                    width=width,
                    height=height,
                    filename_prefix=state.get("filename_prefix"),
                    batch_size=images_per_prompt,
                )
                image_ids = [
                    self.db.add_image(
                        job_id,
                        positive=positive,
                        negative=negative,
                        seed=base_seed,
                        width=width,
                        height=height,
                    )
                    for _ in range(images_per_prompt)
                ]

                try:
                    prompt_id = self.comfyui.submit(wf)
                except Exception as exc:  # noqa: BLE001
                    for iid in image_ids:
                        self.db.update_image(iid, status="failed", error=str(exc))
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
                    per_image = duration / len(saved) if saved else duration

                    for j, s in enumerate(saved[: len(image_ids)]):
                        sub = s.get("subfolder", "")
                        fn = s.get("filename", "")
                        path = f"{sub}/{fn}" if sub else fn
                        iid = image_ids[j]
                        self.db.update_image(
                            iid, status="done", path=path, duration_seconds=per_image
                        )
                        durations.append(per_image)
                        generated += 1
                        self.db.update_job(job_id, images_done=generated)
                        logger.info("job %s — image %s ok (%.1fs)", job_id, iid, per_image)
                        if trace:
                            self.trace_logger.log_image(
                                job_id=job_id,
                                image_id=iid,
                                positive=positive,
                                negative=negative,
                                seed=base_seed,
                                width=width,
                                height=height,
                                status="done",
                                path=path,
                                duration=per_image,
                                test_mode=state.get("test_mode", False),
                            )

                    # images attendues mais absentes du résultat
                    for j in range(len(saved), len(image_ids)):
                        iid = image_ids[j]
                        msg = "image absente du résultat ComfyUI"
                        self.db.update_image(iid, status="failed", error=msg)
                        if trace:
                            self.trace_logger.log_image(
                                job_id=job_id,
                                image_id=iid,
                                positive=positive,
                                negative=negative,
                                seed=base_seed,
                                width=width,
                                height=height,
                                status="failed",
                                error=msg,
                                test_mode=state.get("test_mode", False),
                            )
                except Exception as exc:  # noqa: BLE001
                    for iid in image_ids:
                        self.db.update_image(iid, status="failed", error=str(exc))
                    logger.warning("job %s — lot échoué : %s", job_id, exc)
                    if trace:
                        for iid in image_ids:
                            self.trace_logger.log_image(
                                job_id=job_id,
                                image_id=iid,
                                positive=positive,
                                negative=negative,
                                seed=base_seed,
                                width=width,
                                height=height,
                                status="failed",
                                error=str(exc),
                                test_mode=state.get("test_mode", False),
                            )
        finally:
            if swap:
                try:
                    self.comfyui.free_vram()
                except Exception as exc:  # noqa: BLE001 — ComfyUI peut être tombé
                    logger.warning("job %s — libération VRAM impossible : %s", job_id, exc)

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
        images_per_prompt = max(1, int(state.get("batch", 1)))

        if objective.get("type") == "count":
            target = state.get("target") or 0
            remaining = max(0, target - generated)
            if remaining > 0:
                prompts_to_draft = math.ceil(remaining / images_per_prompt)
                cap = state.get("draft_batch_size")
                if cap:
                    prompts_to_draft = min(prompts_to_draft, cap)
                return {"batch_size": prompts_to_draft, "continue_loop": True}
            return {"continue_loop": False, "status": "done"}

        deadline = state.get("deadline")
        if deadline is None or time.monotonic() >= deadline:
            return {"continue_loop": False, "status": "done"}

        remaining = deadline - time.monotonic()
        cal = self.db.get_calibration(state["workflow"])
        if cal and cal["avg_seconds"] > 0:
            per_prompt = cal["avg_seconds"] * images_per_prompt
            batch = max(1, int(remaining / per_prompt))
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
        positive_prefix: str | None = None,
        negative: str | None = None,
        temperature: float | None = None,
        batch: int = 1,
        min_words: int = 0,
        random_tags: bool = False,
        random_tags_count: int = 15,
        filename_prefix: str | None = None,
        draft_batch_size: int | None = None,
        enable_traces: bool = True,
        preset: str | None = None,
        forced_searches: int = 0,
        test_mode: bool = False,
        random_datasets: list[str] | None = None,
    ) -> int:
        """Crée un job en base et l'enfile dans la file FIFO. Retourne job_id."""
        entry = self.manifest.get(workflow, {})
        if style is None:
            style = entry.get("style", "tags")

        images_target = (
            int(objective["n"]) * batch if objective.get("type") == "count" else None
        )
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
            positive_prefix=positive_prefix,
            negative=negative,
            temperature=temperature,
            batch=batch,
            min_words=min_words,
            random_tags=random_tags,
            random_tags_count=random_tags_count,
            model=model or self.config.llm.model,
            filename_prefix=filename_prefix,
            draft_batch_size=draft_batch_size,
            enable_traces=enable_traces,
            preset=preset,
            forced_searches=forced_searches,
            test_mode=test_mode,
            random_datasets=random_datasets,
            images_target=images_target,
        )
        self.db.update_job(job_id, status="queued")
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
            "positive_prefix": positive_prefix,
            "negative": negative,
            "temperature": temperature,
            "batch": batch,
            "min_words": min_words,
            "random_tags": random_tags,
            "random_tags_count": random_tags_count,
            "filename_prefix": filename_prefix,
            "draft_batch_size": draft_batch_size,
            "enable_traces": enable_traces,
            "preset": preset,
            "forced_searches": forced_searches,
            "test_mode": test_mode,
            "random_datasets": random_datasets,
        }

        self._stop_events[job_id] = threading.Event()
        self._queue.append((job_id, initial))
        self._enqueued.add(job_id)
        return job_id

    def _worker_loop(self) -> None:
        """Worker unique : dépile les jobs (FIFO) ; gère reprise, pause et pause globale."""
        while True:
            if self._global_pause.is_set():
                time.sleep(0.2)
                continue
            if not self._queue:
                time.sleep(0.2)
                continue
            job_id, initial = self._queue.popleft()
            self._enqueued.discard(job_id)
            self._current_job_id = job_id
            try:
                is_resume = initial is None
                if (not is_resume) and self._stop_events.get(job_id, threading.Event()).is_set():
                    self.db.update_job(
                        job_id, status="stopped", error="arrêté avant exécution",
                        finished_at=_now(),
                    )
                    continue
                if is_resume:
                    self.db.update_job(job_id, status="running")
                else:
                    self.db.update_job(job_id, status="running", started_at=_now())
                result = self._run(job_id, initial)
                if result is not None and "__interrupt__" in result:
                    self.db.update_job(job_id, status="paused")
                    logger.info("job %s — mis en pause (reprise possible)", job_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception("worker — erreur sur le job %s : %s", job_id, exc)
            finally:
                self._current_job_id = None

    def _run(self, job_id: int, initial: OrchestratorState | None) -> dict | None:
        run_config = {"configurable": {"thread_id": str(job_id)}}
        try:
            return self._graph.invoke(initial, config=run_config)
        except Exception as exc:  # noqa: BLE001
            logger.exception("job %s — erreur non capturée", job_id)
            self.db.update_job(job_id, status="failed", error=str(exc), finished_at=_now())
            return None

    def stop(self, job_id: int) -> None:
        """Demande un arrêt propre (entre deux images, ou avant exécution si en file)."""
        event = self._stop_events.get(job_id)
        if event is not None:
            logger.info("job %s — demande d'arrêt", job_id)
            event.set()
        job = self.db.get_job(job_id)
        if job is not None and job["status"] == "queued":
            self.db.update_job(
                job_id, status="stopped", error="arrêté avant exécution",
                finished_at=_now(),
            )

    def resume(self, job_id: int) -> None:
        """Reprend un job mis en pause (relance le graphe depuis le checkpoint)."""
        event = self._stop_events.get(job_id)
        if event is not None:
            event.clear()
        else:
            self._stop_events[job_id] = threading.Event()
        job = self.db.get_job(job_id)
        if job is not None and job["status"] == "paused":
            self.db.update_job(job_id, status="queued")
            self._queue.appendleft((job_id, None))
            self._enqueued.add(job_id)
            logger.info("job %s — reprise demandée", job_id)

    def pause_all(self) -> None:
        """Pause globale : gèle la file et met en pause le job en cours."""
        self._global_pause.set()
        current = self._current_job_id
        if current is not None:
            event = self._stop_events.get(current)
            if event is not None:
                event.set()
        logger.info("pause globale demandée (job en cours : %s)", current)

    def resume_all(self) -> None:
        """Reprend tout : dégèle la file et remet en file jobs 'paused' et 'queued'.

        Interroge la base (pas la file en mémoire) pour survivre à un redémarrage.
        Les jobs 'paused' reprennent via leur checkpoint, les 'queued' via leur état
        reconstruit depuis la base.
        """
        self._global_pause.clear()
        rows = sorted(self.db.list_jobs(limit=100_000), key=lambda r: r["id"])
        items: list[tuple[int, OrchestratorState | None]] = []
        for row in rows:
            jid = row["id"]
            status = row.get("status")
            if status == "paused":
                items.append((jid, None))
            elif status == "queued" and jid not in self._enqueued:
                items.append((jid, self._state_from_row(row)))
            else:
                continue
            if jid not in self._stop_events:
                self._stop_events[jid] = threading.Event()
            self._stop_events[jid].clear()
            self.db.update_job(jid, status="queued")
        for jid, initial in reversed(items):
            self._queue.appendleft((jid, initial))
            self._enqueued.add(jid)
        logger.info("reprise globale demandée (%d job(s) remis en file)", len(items))

    def _recover_pending_jobs(self) -> None:
        """Après un redémarrage, nettoie les jobs laissés en plan.

        'running' → 'paused' (le worker est mort ; reprise possible via checkpoint).
        'queued' et 'paused' restent tels quels : ils seront repris par resume_all().
        """
        for row in self.db.list_jobs(limit=100_000):
            if row.get("status") == "running":
                self.db.update_job(row["id"], status="paused")
                self._stop_events[row["id"]] = threading.Event()
                logger.info(
                    "reprise à froid — job %s marqué 'paused' (il était 'running')",
                    row["id"],
                )

    def _state_from_row(self, row: dict[str, Any]) -> OrchestratorState:
        """Reconstruit l'état initial d'un job depuis sa ligne en base."""
        def _json(value: Any) -> Any:
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    pass
            return None

        seed_raw = row.get("seed")
        try:
            seed = int(seed_raw) if seed_raw not in (None, "") else None
        except (TypeError, ValueError):
            seed = None

        return {
            "job_id": row["id"],
            "objective": _json(row.get("objective")) or {},
            "theme": row.get("theme") or "",
            "workflow": row.get("workflow") or "",
            "style": row.get("style"),
            "orientation": row.get("orientation") or "carre",
            "datasets": _json(row.get("datasets")) or [],
            "swap_vram": bool(row.get("swap_vram")),
            "model": row.get("model"),
            "seed": seed,
            "positive_suffix": row.get("positive_suffix"),
            "positive_prefix": row.get("positive_prefix"),
            "negative": row.get("negative"),
            "temperature": row.get("temperature"),
            "batch": int(row.get("batch") or 1),
            "min_words": int(row.get("min_words") or 0),
            "random_tags": bool(row.get("random_tags")),
            "random_tags_count": int(row.get("random_tags_count") or 15),
            "filename_prefix": row.get("filename_prefix"),
            "draft_batch_size": row.get("draft_batch_size"),
            "enable_traces": bool(row.get("enable_traces", True)),
            "preset": row.get("preset"),
            "forced_searches": int(row.get("forced_searches") or 0),
            "test_mode": bool(row.get("test_mode")),
            "random_datasets": _json(row.get("random_datasets")) or [],
        }
