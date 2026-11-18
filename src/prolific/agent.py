"""Agent de rédaction de prompts : boucle LLM ↔ outils (search_terms)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from prolific.prompting import (
    PROMPTS_DIR,
    build_draft_system,
    build_draft_user,
    normalize_draft,
)

MAX_TOOL_ITERATIONS = 6


def draft_prompts_agent(
    llm: Any,
    tools: list,
    *,
    n: int,
    theme: str,
    style: str = "tags",
    datasets: list[str] | None = None,
    history: list[str] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
) -> list[str]:
    """Rédige `n` prompts sur un thème via une boucle LLM↔outils, puis normalise.

    Le LLM peut appeler les outils fournis (ex. search_terms) autant de fois que
    nécessaire, puis doit produire le JSON final {"prompts": [...]} qui est normalisé.
    """
    system = build_draft_system(style=style, datasets=datasets, prompts_dir=prompts_dir)
    user = build_draft_user(n=n, theme=theme, history=history, prompts_dir=prompts_dir)

    llm_with_tools = llm.bind_tools(tools) if tools else llm
    messages: list = [SystemMessage(content=system), HumanMessage(content=user)]

    resp: AIMessage | None = None
    for _ in range(MAX_TOOL_ITERATIONS):
        resp = llm_with_tools.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            break
        for call in resp.tool_calls:
            tool = next((t for t in tools if t.name == call["name"]), None)
            content = (
                str(tool.invoke(call["args"]))
                if tool
                else f"Outil inconnu : {call['name']}"
            )
            messages.append(ToolMessage(content=content, tool_call_id=call["id"]))

    if resp is None:
        return normalize_draft("", style)

    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    return normalize_draft(raw, style)
