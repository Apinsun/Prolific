"""Bibliothèque de workflows ComfyUI (manifest.json + injection de slots)."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


class WorkflowError(Exception):
    """Erreur liée à la bibliothèque de workflows."""


def load_manifest(workflows_dir: Path) -> dict[str, Any]:
    """Charge workflows/manifest.json ({} si absent)."""
    manifest = workflows_dir / "manifest.json"
    if not manifest.exists():
        return {}
    return json.loads(manifest.read_text(encoding="utf-8"))


def list_workflow_names(manifest: dict[str, Any]) -> list[str]:
    return sorted(manifest.keys())


def resolve_format(manifest: dict[str, Any], name: str, orientation: str) -> tuple[int, int]:
    """Résout une orientation (carre/paysage/portrait/random) en (largeur, hauteur)."""
    if name not in manifest:
        raise WorkflowError(f"Workflow inconnu : {name!r}")
    formats = manifest[name].get("formats", {})
    if not formats:
        raise WorkflowError(f"Aucun format déclaré pour {name!r}")
    if orientation == "random":
        orientation = random.choice(list(formats.keys()))
    if orientation not in formats:
        raise WorkflowError(
            f"Format invalide {orientation!r} pour {name!r}. Valides : {list(formats)}"
        )
    width_s, height_s = formats[orientation].split("x")
    return int(width_s), int(height_s)


def patch_workflow(
    manifest: dict[str, Any],
    workflows_dir: Path,
    name: str,
    *,
    positive: str,
    negative: str | None = None,
    orientation: str = "carre",
    seed: int | None = None,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Charge un workflow et y injecte prompt/dimensions/seeds via les slots déclarés.

    Ne touche qu'aux slots déclarés dans le manifest : checkpoint, LoRA, négatif par
    défaut, etc. restent figés dans le fichier du workflow.
    """
    if name not in manifest:
        raise WorkflowError(f"Workflow inconnu : {name!r}")

    entry = manifest[name]
    wf_path = workflows_dir / entry["file"]
    if not wf_path.exists():
        raise WorkflowError(f"Fichier workflow introuvable : {wf_path}")

    wf = json.loads(wf_path.read_text(encoding="utf-8"))
    slots = entry.get("slots", {})

    def set_slot(slot: str, value: Any) -> None:
        if slot not in slots:
            return
        s = slots[slot]
        node = str(s["node"])
        if node in wf and "inputs" in wf[node]:
            wf[node]["inputs"][s["field"]] = value

    if width is None or height is None:
        width, height = resolve_format(manifest, name, orientation)

    set_slot("positive", positive)
    if negative:
        set_slot("negative", negative)
    set_slot("width", width)
    set_slot("height", height)

    # Seeds : chaque KSampler listé est resédé (même seed si fourni, sinon aléatoire)
    for seed_node in slots.get("seeds", []):
        node = str(seed_node)
        if node in wf and "inputs" in wf[node]:
            value = seed if seed is not None else random.randint(0, 2**63 - 1)
            wf[node]["inputs"]["seed"] = value

    return wf
