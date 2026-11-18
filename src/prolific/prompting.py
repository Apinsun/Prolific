"""Rédaction de prompts : templates, interpolation, normalisation.

Les directives vivent dans prompts/ (éditables sans toucher au code). Le code se
contente de charger des templates et d'interpoler des placeholders `$...`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any

from prolific.clients.ollama import OllamaClient

PROMPTS_DIR = Path("prompts")

_STYLE_FILES = {
    "tags": "style_tags.txt",
    "prose": "style_prose.txt",
    "hybrid": "style_hybrid.txt",
}


class PromptingError(Exception):
    """Sortie du LLM inexploitable."""


@dataclass
class Prompt:
    positive: str
    negative: str | None = None


# ── templates ────────────────────────────────────────────────────────
def load_template(name: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    path = prompts_dir / name
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _render(template: str, **kwargs: Any) -> str:
    return Template(template).substitute(**kwargs)


def build_draft_system(
    *,
    style: str = "tags",
    datasets: list[str] | None = None,
    random_terms: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
) -> str:
    style_file = _STYLE_FILES.get(style, _STYLE_FILES["tags"])
    style_directive = load_template(style_file, prompts_dir)

    if datasets:
        hint = _render(
            load_template("datasets_hint.txt", prompts_dir),
            datasets=", ".join(datasets),
        )
    else:
        hint = ""

    if random_terms:
        random_hint = _render(
            load_template("random_hint.txt", prompts_dir),
            terms=", ".join(random_terms),
        )
    else:
        random_hint = ""

    return _render(
        load_template("draft_system.txt", prompts_dir),
        style_directive=style_directive,
        datasets_hint=hint,
        random_hint=random_hint,
    )


def build_draft_user(
    *,
    n: int,
    theme: str,
    history: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
) -> str:
    base = _render(load_template("draft_user.txt", prompts_dir), n=n, theme=theme)
    if history:
        hint = _render(
            load_template("history_hint.txt", prompts_dir),
            prompts="\n".join(f"- {p}" for p in history),
        )
        return f"{base}\n\n{hint}"
    return base


# ── normalisation ────────────────────────────────────────────────────
def _extract_json(text: str) -> Any:
    """Extrait un objet/liste JSON de la sortie (fences markdown, prose autour)."""
    text = text.strip()

    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    # premier objet `{...}` équilibré
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except (json.JSONDecodeError, TypeError):
                        break
        start = text.find("{", start + 1)

    return None


def clean_prompt(text: str, style: str = "tags") -> str:
    """Nettoie UN prompt selon son style (forme uniquement, jamais le contenu)."""
    text = text.strip()
    text = re.sub(r"^(prompt|positive|sujet)\s*:\s*", "", text, flags=re.IGNORECASE).strip()

    if style == "tags":
        tags: list[str] = []
        seen: set[str] = set()
        for part in re.split(r"[,\n]+", text):
            part = part.strip()
            if part and part not in seen:
                seen.add(part)
                tags.append(part)
        return ", ".join(tags)

    # prose / hybrid : une seule ligne, espaces normalisés
    return re.sub(r"\s+", " ", text).strip()


def dedupe(prompts: list[str], history: list[str]) -> list[str]:
    """Retire les doublons exacts (vs historique et au sein du lot), en gardant l'ordre."""
    seen = set(history)
    out: list[str] = []
    for prompt in prompts:
        if prompt in seen:
            continue
        seen.add(prompt)
        out.append(prompt)
    return out


def dedupe_tags(prompt: str) -> str:
    """Dédoublonne des tags par token exact (entre virgules), en gardant l'ordre.

    Ne confond pas les sous-chaînes : 'black', 'black hair' et 'black armor' restent
    trois tags distincts.
    """
    seen: set[str] = set()
    tags: list[str] = []
    for part in prompt.split(","):
        tag = part.strip()
        if tag and tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return ", ".join(tags)


def compose_positive(positive: str, suffix: str | None, style: str = "tags") -> str:
    """Concatène un suffixe au prompt positif (séparateur selon le style)."""
    if not suffix:
        return positive
    suffix = suffix.strip().strip(",").strip()
    if not suffix:
        return positive
    separator = " " if style == "prose" else ", "
    result = f"{positive}{separator}{suffix}"
    if style == "tags":
        result = dedupe_tags(result)
    return result


def normalize_draft_full(
    raw: str, style: str = "tags"
) -> tuple[list[str], list[str | None]]:
    """Transforme la sortie brute en (prompts nettoyés, orientations par prompt)."""
    data = _extract_json(raw)

    raw_prompts: list[str] = []
    orientations: list[str | None] = []

    if isinstance(data, list):
        raw_prompts = [str(p) for p in data]
    elif isinstance(data, dict) and isinstance(data.get("prompts"), list):
        raw_prompts = [str(p) for p in data["prompts"]]
        orient_list = data.get("orientations")
        if isinstance(orient_list, list) and len(orient_list) == len(raw_prompts):
            orientations = [
                o if o in ("carre", "paysage", "portrait") else None
                for o in orient_list
            ]

    cleaned = [clean_prompt(p, style) for p in raw_prompts]
    prompts: list[str] = []
    kept_orientations: list[str | None] = []
    for i, p in enumerate(cleaned):
        if p:
            prompts.append(p)
            kept_orientations.append(orientations[i] if orientations else None)

    if prompts:
        return prompts, kept_orientations

    # repli : une ligne = un prompt (sans orientation)
    prompts = [p for p in (clean_prompt(line, style) for line in raw.splitlines()) if p]
    if not prompts:
        raise PromptingError("Aucun prompt exploitable dans la sortie du LLM.")
    return prompts, [None] * len(prompts)


def normalize_draft(raw: str, style: str = "tags") -> list[str]:
    """Rétrocompat : retourne seulement les prompts (sans les orientations)."""
    return normalize_draft_full(raw, style)[0]


def normalize_prompt(raw: str, style: str = "tags") -> Prompt:
    """Normalise un unique prompt (avec négatif optionnel)."""
    data = _extract_json(raw)

    if isinstance(data, dict):
        positive = data.get("positive") or data.get("prompt") or ""
        negative = data.get("negative") or data.get("negative_prompt") or None
    else:
        positive = raw
        negative = None

    positive = clean_prompt(str(positive), style)
    if not positive:
        raise PromptingError("Prompt positif vide après normalisation.")
    if negative:
        negative = clean_prompt(str(negative), "tags")

    return Prompt(positive=positive, negative=negative)


# ── appel LLM ────────────────────────────────────────────────────────
def draft_prompts(
    ollama: OllamaClient,
    *,
    model: str,
    n: int,
    theme: str,
    style: str = "tags",
    datasets: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
) -> list[str]:
    """Rédige n prompts sur un thème, via le LLM, et normalise le résultat."""
    system = build_draft_system(style=style, datasets=datasets, prompts_dir=prompts_dir)
    user = build_draft_user(n=n, theme=theme, prompts_dir=prompts_dir)
    raw = ollama.generate(model, user, system=system, format="json")
    return normalize_draft(raw, style)
