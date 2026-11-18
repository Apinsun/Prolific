"""RAG : indexation de datasets CSV en ChromaDB + recherche de termes.

Un dataset = un CSV (`term, definition, category`, catégorie optionnelle) indexé dans
sa propre collection ChromaDB `rag_<nom>`. Embeddings via Ollama (`/api/embed`).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import chromadb

from prolific.clients.ollama import OllamaClient


class RagError(Exception):
    """Erreur liée au RAG (CSV invalide, embedding en échec…)."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


class RagIndex:
    def __init__(self, chroma_dir: Path, ollama: OllamaClient, embed_model: str) -> None:
        self.ollama = ollama
        self.embed_model = embed_model
        self.client = chromadb.PersistentClient(path=str(chroma_dir))

    @staticmethod
    def _collection_name(dataset: str) -> str:
        return f"rag_{dataset}"

    # ── indexation ──────────────────────────────────────────────────────
    def index_csv(
        self,
        csv_path: Path,
        dataset: str | None = None,
        *,
        batch_size: int = 50,
    ) -> int:
        """Indexe un CSV. Retourne le nombre d'entrées. Ré-écrase la collection."""
        dataset = dataset or csv_path.stem
        terms, definitions, categories = self._load_csv(csv_path)

        if not terms:
            raise RagError(f"CSV vide ou colonnes invalides : {csv_path}")

        name = self._collection_name(dataset)
        self._drop_collection(name)
        collection = self.client.create_collection(name=name, metadata={"hnsw:space": "cosine"})

        for i in range(0, len(terms), batch_size):
            batch_terms = terms[i : i + batch_size]
            batch_defs = definitions[i : i + batch_size]
            batch_cats = categories[i : i + batch_size]
            texts = [f"{t}: {d}" for t, d in zip(batch_terms, batch_defs)]
            embeddings = self.ollama.embed(self.embed_model, texts)
            collection.add(
                ids=[f"{dataset}_{j}" for j in range(i, i + len(batch_terms))],
                embeddings=embeddings,
                documents=batch_defs,
                metadatas=[
                    {"term": t, "category": c, "dataset": dataset}
                    for t, c in zip(batch_terms, batch_cats)
                ],
            )

        return len(terms)

    def index_all(self, datasets_dir: Path) -> list[str]:
        """Indexe tous les CSV d'un dossier. Retourne les noms de datasets indexés."""
        indexed: list[str] = []
        for csv_path in sorted(datasets_dir.glob("*.csv")):
            self.index_csv(csv_path, dataset=csv_path.stem)
            indexed.append(csv_path.stem)
        return indexed

    # ── recherche ───────────────────────────────────────────────────────
    def search(
        self,
        concept: str,
        datasets: list[str] | None = None,
        *,
        top_k: int = 10,
        threshold: float = 0.3,
    ) -> list[dict[str, Any]]:
        """Recherche un concept sur les datasets donnés (tous si None)."""
        query_embedding = self.ollama.embed(self.embed_model, [concept])[0]

        if datasets is None:
            datasets = self.list_datasets()

        results: list[dict[str, Any]] = []
        for dataset in datasets:
            try:
                collection = self.client.get_collection(self._collection_name(dataset))
            except Exception:
                continue  # dataset non indexé : on l'ignore
            hit = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k * 2, 100),
                include=["documents", "metadatas", "distances"],
            )
            for i in range(len(hit["ids"][0])):
                meta = hit["metadatas"][0][i]
                similarity = 1.0 - hit["distances"][0][i]
                if similarity >= threshold:
                    results.append(
                        {
                            "term": meta.get("term", ""),
                            "definition": hit["documents"][0][i],
                            "category": meta.get("category") or None,
                            "dataset": dataset,
                            "similarity": round(similarity, 4),
                        }
                    )

        results.sort(key=lambda r: r["similarity"], reverse=True)
        return results[:top_k]

    # ── utilitaires ─────────────────────────────────────────────────────
    def list_datasets(self) -> list[str]:
        prefix = "rag_"
        return sorted(
            c.name[len(prefix) :]
            for c in self.client.list_collections()
            if c.name.startswith(prefix)
        )

    @staticmethod
    def _load_csv(csv_path: Path) -> tuple[list[str], list[str], list[str]]:
        """Lit un CSV et accepte les variantes term/tag et definition/description."""
        terms: list[str] = []
        definitions: list[str] = []
        categories: list[str] = []
        with csv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                term = _clean(row.get("term") or row.get("tag"))
                definition = _clean(
                    row.get("definition")
                    or row.get("description")
                    or row.get("description_propre")
                )
                category = _clean(row.get("category"))
                if term and definition:
                    terms.append(term)
                    definitions.append(definition)
                    categories.append(category)
        return terms, definitions, categories

    def _drop_collection(self, name: str) -> None:
        try:
            self.client.delete_collection(name)
        except Exception:
            pass
