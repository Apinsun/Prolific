"""Agent de rédaction de prompts : boucle LLM ↔ outils + raffinement si trop court."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from prolific.prompting import (
    PROMPTS_DIR,
    PromptingError,
    build_draft_system,
    build_draft_user,
    normalize_draft_full,
)

MAX_TOOL_ITERATIONS = 6
MAX_REFINE_ITERATIONS = 3

logger = logging.getLogger(__name__)


def _hr(title: str) -> str:
    """Séparateur visuel pour délimiter les blocs dans les logs."""
    return f"{'─' * 20} {title} {'─' * 20}"


def _count_units(prompt: str, style: str) -> int:
    """Nombre d'unités : tags (séparés par virgules) ou mots (prose)."""
    if style == "tags":
        return len([t for t in prompt.split(",") if t.strip()])
    return len(prompt.split())


def _run_until_answer(
    llm_with_tools: Any,
    tools: list,
    messages: list,
    tool_calls_log: list[dict],
    min_tool_calls: int = 0,
) -> AIMessage | None:
    """Boucle LLM↔outils jusqu'à une réponse finale (ou épuisement des itérations).

    Si `min_tool_calls` > 0, le LLM est relancé tant qu'il n'a pas effectué ce nombre
    d'appels d'outils (recherches forcées).
    """
    resp: AIMessage | None = None
    done_calls = 0
    max_rounds = max(MAX_TOOL_ITERATIONS, min_tool_calls + 1)
    for round_idx in range(max_rounds):
        resp = llm_with_tools.invoke(messages)
        messages.append(resp)

        content = resp.content if isinstance(resp.content, str) else str(resp.content)
        if content:
            logger.info("%s\n%s", _hr(f"RÉPONSE LLM (tour {round_idx + 1})"), content)

        extra = getattr(resp, "additional_kwargs", None) or {}
        thinking = extra.get("reasoning_content") or extra.get("thinking")
        if thinking:
            logger.info("%s\n%s", _hr(f"RAISONNEMENT LLM (tour {round_idx + 1})"), thinking)

        if not resp.tool_calls:
            if done_calls < min_tool_calls:
                remaining = min_tool_calls - done_calls
                messages.append(HumanMessage(content=(
                    f"Tu n'as pas encore effectué le nombre de recherches demandé. "
                    f"Effectue encore {remaining} recherche(s) avec les outils "
                    f"(search_terms, list_categories ou random_term) avant de rédiger."
                )))
                continue
            break

        logger.info("llm (%d) — %d tool call(s)", round_idx + 1, len(resp.tool_calls))
        for call in resp.tool_calls:
            tool = next((t for t in tools if t.name == call["name"]), None)
            if tool is None:
                content = f"Outil inconnu : {call['name']}"
                logger.warning("tool call inconnu : %s", call["name"])
            else:
                try:
                    content = str(tool.invoke(call["args"]))
                except Exception as exc:  # noqa: BLE001
                    content = f"Erreur de l'outil : {exc}"
                    logger.error("tool call %s a échoué : %s", call["name"], exc)
            done_calls += 1
            tool_calls_log.append(
                {"name": call["name"], "args": call.get("args"), "result": content}
            )
            logger.info(
                "APPEL D'OUTIL : %s(%s)",
                call["name"], call.get("args"),
            )
            logger.info("RÉSULTAT D'OUTIL : %s", content[:500])
            messages.append(ToolMessage(content=content, tool_call_id=call["id"]))
    return resp


def _looks_garbage(prompts: list[str]) -> bool:
    """Détecte des artefacts de parse (fences, crochets, guillemets englobants)."""
    for p in prompts:
        p = p.strip()
        if not p or len(p) < 8:
            return True
        if p.startswith(("```", "{", "[")):
            return True
        if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'":
            return True
    return False


def _parse_draft(
    llm_with_tools: Any,
    tools: list,
    messages: list,
    tool_calls_log: list[dict],
    raw: str,
    style: str,
    max_retries: int = 2,
) -> tuple[list[str], list[str | None]]:
    """Normalise la sortie LLM, avec re-demande au LLM si elle est invalide/artefactée."""
    for _ in range(max_retries + 1):
        try:
            prompts, orientations = normalize_draft_full(raw, style)
        except PromptingError:
            prompts, orientations = [], []
        if prompts and not _looks_garbage(prompts):
            return prompts, orientations
        messages.append(HumanMessage(content=(
            "Ta réponse n'est pas du JSON valide ou contient des artefacts "
            "(fences markdown, crochets, guillemets englobants). Réponds UNIQUEMENT "
            "avec un objet JSON {\"prompts\": [\"...\", \"...\"]} complet et valide, "
            "sans rien autour."
        )))
        resp = _run_until_answer(llm_with_tools, tools, messages, tool_calls_log)
        if resp is None:
            break
        raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    try:
        return normalize_draft_full(raw, style)
    except PromptingError:
        return [], []


def _build_refine_message(short_prompts: list[str], min_units: int, style: str, tool: Any) -> str:
    """Construit la consigne d'enrichissement (glossaire RAG dédoublonné + définitions)."""
    unit_label = "tags" if style == "tags" else "mots"
    lines = "\n".join(f"- {p}" for p in short_prompts)
    glossary: dict[str, str] = {}
    if tool is not None:
        seen_concepts: set[str] = set()
        for p in short_prompts:
            if p in seen_concepts:
                continue
            seen_concepts.add(p)
            try:
                result = tool.invoke({"concept": p, "top_k": 6})
                data = json.loads(result)
                for item in data:
                    if isinstance(item, dict) and item.get("term"):
                        term = item["term"]
                        if term not in glossary:
                            glossary[term] = item.get("definition") or ""
            except Exception:
                pass
    suggestions = ""
    if glossary:
        entries = "\n".join(
            f"- {t}" + (f" : {d}" if d else "")
            for t, d in list(glossary.items())[:40]
        )
        suggestions = (
            f"\nTermes suggérés (avec définitions, dédoublonnés) :\n{entries}\n"
        )
    return (
        f"Les prompts suivants sont trop courts (moins de {min_units} {unit_label}). "
        f"Enrichis-les en ajoutant plus de détails (apparence, pose, environnement, "
        f"éclairage, style). Utilise les termes suggérés ci-dessous s'ils sont pertinents.\n\n"
        f"{lines}\n{suggestions}\n"
        f"Réponds UNIQUEMENT au format JSON : {{\"prompts\": [\"...\", \"...\"]}} "
        f"avec uniquement les versions enrichies, dans le même ordre."
    )


def _to_conversation(messages: list) -> list[dict]:
    """Convertit l'historique en liste {role, content, tool_calls?} (format fine-tuning)."""
    conv: list[dict] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            role = "system"
        elif isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
        elif isinstance(m, ToolMessage):
            role = "tool"
        else:
            role = "unknown"
        entry: dict = {
            "role": role,
            "content": m.content if isinstance(m.content, str) else str(m.content),
        }
        if isinstance(m, AIMessage) and m.tool_calls:
            entry["tool_calls"] = m.tool_calls
        extra = getattr(m, "additional_kwargs", None) or {}
        if extra.get("reasoning_content"):
            entry["thinking"] = extra["reasoning_content"]
        conv.append(entry)
    return conv


def draft_prompts_agent(
    llm: Any,
    tools: list,
    *,
    n: int,
    theme: str,
    style: str = "tags",
    datasets: list[str] | None = None,
    history: list[str] | None = None,
    min_words: int = 0,
    random_terms: list[list[str]] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
    preset: str | None = None,
    forced_searches: int = 0,
) -> tuple[list[str], list[str | None], list[dict], list[dict]]:
    """Rédige `n` prompts via une boucle LLM↔outils, puis raffine si trop courts.

    Retourne (prompts, orientations, tool_calls, conversation). La conversation contient
    tout l'échange (system, user, assistant, tool) — utile pour le fine-tuning.
    """
    system = build_draft_system(
        style=style, datasets=datasets, random_terms=random_terms, prompts_dir=prompts_dir,
        preset=preset,
    )
    user = build_draft_user(n=n, theme=theme, history=history, prompts_dir=prompts_dir)
    if forced_searches > 0:
        user += (
            f"\n\nIMPORTANT : effectue exactement {forced_searches} recherches avec les "
            f"outils (search_terms, list_categories ou random_term) avant de rédiger les prompts."
        )
    logger.info("%s\n%s", _hr("SYSTEM — envoyé au LLM"), system)
    logger.info("%s\n%s", _hr("USER — envoyé au LLM"), user)

    llm_with_tools = llm.bind_tools(tools) if tools else llm
    messages: list = [SystemMessage(content=system), HumanMessage(content=user)]
    tool_calls_log: list[dict] = []

    resp = _run_until_answer(
        llm_with_tools, tools, messages, tool_calls_log,
        min_tool_calls=forced_searches,
    )
    raw = (
        resp.content if resp is not None and isinstance(resp.content, str)
        else str(resp.content) if resp is not None else ""
    )
    prompts, orientations = _parse_draft(
        llm_with_tools, tools, messages, tool_calls_log, raw, style
    )

    # Raffinement des prompts trop courts
    for _ in range(MAX_REFINE_ITERATIONS):
        if min_words <= 0:
            break
        short = [
            (i, p) for i, p in enumerate(prompts) if _count_units(p, style) < min_words
        ]
        if not short:
            break
        refine = _build_refine_message(
            [p for _, p in short], min_words, style, tools[0] if tools else None
        )
        logger.info(
            "%s\n%s",
            _hr(f"HINT RAFFINEMENT — envoyé au LLM ({len(short)} prompts)"),
            refine,
        )
        messages.append(HumanMessage(content=refine))
        resp = _run_until_answer(llm_with_tools, tools, messages, tool_calls_log)
        if resp is None:
            break
        raw = resp.content if isinstance(resp.content, str) else str(resp.content)
        refined, _ = _parse_draft(
            llm_with_tools, tools, messages, tool_calls_log, raw, style
        )
        if not refined:
            break
        for (idx, _), new_p in zip(short, refined):
            if idx < len(prompts):
                prompts[idx] = new_p

    return prompts, orientations, tool_calls_log, _to_conversation(messages)
