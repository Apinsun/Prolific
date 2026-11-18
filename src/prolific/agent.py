"""Agent de rédaction de prompts : boucle LLM ↔ outils (search_terms)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from prolific.prompting import (
    PROMPTS_DIR,
    build_draft_system,
    build_draft_user,
    normalize_draft_full,
)

MAX_TOOL_ITERATIONS = 6

logger = logging.getLogger(__name__)


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
) -> tuple[list[str], list[str | None], list[dict]]:
    """Rédige `n` prompts via une boucle LLM↔outils, puis normalise.

    Retourne (prompts, orientations, tool_calls). Le LLM peut appeler les outils
    fournis (ex. search_terms) autant de fois que nécessaire, puis produire le JSON
    final {"prompts": [...], "orientations": [...]}.
    """
    system = build_draft_system(style=style, datasets=datasets, prompts_dir=prompts_dir)
    user = build_draft_user(n=n, theme=theme, history=history, prompts_dir=prompts_dir)

    llm_with_tools = llm.bind_tools(tools) if tools else llm
    messages: list = [SystemMessage(content=system), HumanMessage(content=user)]

    tool_calls: list[dict] = []
    resp: AIMessage | None = None
    for _ in range(MAX_TOOL_ITERATIONS):
        resp = llm_with_tools.invoke(messages)
        messages.append(resp)
        if not resp.tool_calls:
            break
        for call in resp.tool_calls:
            tool = next((t for t in tools if t.name == call["name"]), None)
            logger.info("tool call : %s(%s)", call["name"], call.get("args"))
            content = (
                str(tool.invoke(call["args"]))
                if tool
                else f"Outil inconnu : {call['name']}"
            )
            tool_calls.append(
                {"name": call["name"], "args": call.get("args"), "result": content}
            )
            logger.info("tool result (%s) : %s", call["name"], content[:200])
            messages.append(ToolMessage(content=content, tool_call_id=call["id"]))

    if resp is None:
        prompts, orientations = normalize_draft_full("", style)
        return prompts, orientations, tool_calls

    raw = resp.content if isinstance(resp.content, str) else str(resp.content)
    prompts, orientations = normalize_draft_full(raw, style)
    return prompts, orientations, tool_calls
