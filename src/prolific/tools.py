"""Outils exposés au LLM (tool calling)."""

from __future__ import annotations

import json
from typing import Callable

from langchain_core.tools import tool

from prolific.rag import RagIndex


def make_search_terms_tool(rag: RagIndex, datasets: list[str]) -> Callable:
    """Fabrique l'outil `search_terms`, lié à un index RAG et des datasets actifs."""

    @tool
    def search_terms(concept: str, top_k: int = 10, category: str | None = None) -> str:
        """Cherche des termes précis (tags ou vocabulaire) correspondant à un concept.

        Args:
            concept: Concept ou description en langage naturel à rechercher.
            top_k: Nombre maximum de résultats à renvoyer.
            category: Catégorie EXACTE à filtrer — appelle d'abord list_categories pour
                connaître les catégories valides (ex. 'Haut', 'Posture', 'Position').
                Si incertain, OMETS ce paramètre (recherche sans filtre).

        Returns:
            Chaîne JSON : liste de {term, definition, category, dataset, similarity}.
        """
        results = rag.search(concept, datasets=datasets, top_k=top_k, category=category)
        if not results:
            return "Aucun terme trouvé."
        return json.dumps(results, ensure_ascii=False)

    return search_terms


def make_list_categories_tool(rag: RagIndex, datasets: list[str]) -> Callable:
    """Fabrique l'outil `list_categories` (catégories disponibles des datasets actifs)."""

    @tool
    def list_categories() -> str:
        """Liste les catégories disponibles dans les datasets actifs.

        Returns:
            Chaîne JSON : liste de catégories (ex. ["vêtement", "accessoire"]).
        """
        return json.dumps(rag.list_categories(datasets), ensure_ascii=False)

    return list_categories


def make_random_term_tool(rag: RagIndex, datasets: list[str]) -> Callable:
    """Fabrique l'outil `random_term` (terme aléatoire, éventuellement par catégorie)."""

    @tool
    def random_term(category: str | None = None, count: int = 3) -> str:
        """Tire des termes aléatoires (dans une catégorie si précisée).

        Args:
            category: Catégorie EXACTE (voir list_categories). Si incertain, omets-la.
            count: Nombre de termes à tirer.

        Returns:
            Chaîne JSON : liste de termes.
        """
        terms = rag.random_terms(datasets, k=count, category=category)
        return json.dumps(terms, ensure_ascii=False)

    return random_term
