"""Outils exposés au LLM (tool calling)."""

from __future__ import annotations

import json
from typing import Callable

from langchain_core.tools import tool

from prolific.rag import RagIndex


def make_search_terms_tool(rag: RagIndex, datasets: list[str]) -> Callable:
    """Fabrique l'outil `search_terms`, lié à un index RAG et des datasets actifs."""

    @tool
    def search_terms(concept: str, top_k: int = 10) -> str:
        """Cherche des termes précis (tags ou vocabulaire) correspondant à un concept.

        Args:
            concept: Concept ou description en langage naturel à rechercher.
            top_k: Nombre maximum de résultats à renvoyer.

        Returns:
            Chaîne JSON : liste de {term, definition, category, dataset, similarity}.
        """
        results = rag.search(concept, datasets=datasets, top_k=top_k)
        if not results:
            return "Aucun terme trouvé."
        return json.dumps(results, ensure_ascii=False)

    return search_terms
