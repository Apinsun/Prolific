"""RAG : indexation de datasets CSV en ChromaDB + recherche de termes.

Un dataset = un CSV (`term, definition, category`, catégorie optionnelle) indexé dans
sa propre collection ChromaDB `rag_<nom>`. Embeddings via Ollama (`/api/embed`).
"""

from __future__ import annotations

import csv
import json
import random
import re
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
        distinct_categories = sorted({c for cats in categories for c in cats})
        collection = self.client.create_collection(
            name=name,
            metadata={
                "hnsw:space": "cosine",
                "categories": json.dumps(distinct_categories),
            },
        )

        for i in range(0, len(terms), batch_size):
            batch_terms = terms[i : i + batch_size]
            batch_defs = definitions[i : i + batch_size]
            batch_cats = categories[i : i + batch_size]
            texts = [f"{t}: {d}" for t, d in zip(batch_terms, batch_defs)]
            embeddings = self.ollama.embed(self.embed_model, texts)
            metadatas = []
            for t, c in zip(batch_terms, batch_cats):
                meta: dict[str, Any] = {"term": t, "dataset": dataset}
                if c:
                    meta["category"] = c
                metadatas.append(meta)
            collection.add(
                ids=[f"{dataset}_{j}" for j in range(i, i + len(batch_terms))],
                embeddings=embeddings,
                documents=batch_defs,
                metadatas=metadatas,
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
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Recherche un concept (optionnellement filtré par catégorie)."""
        query_embedding = self.ollama.embed(self.embed_model, [concept])[0]

        if datasets is None:
            datasets = self.list_datasets()

        results: list[dict[str, Any]] = []
        for dataset in datasets:
            try:
                collection = self.client.get_collection(self._collection_name(dataset))
            except Exception:
                continue  # dataset non indexé : on l'ignore
            kwargs: dict[str, Any] = {}
            if category:
                kwargs["where"] = {"category": {"$contains": category}}
            hit = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k * 2, 100),
                include=["documents", "metadatas", "distances"],
                **kwargs,
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

    def list_categories(self, datasets: list[str] | None = None) -> list[str]:
        """Liste les catégories distinctes des datasets (tous si None)."""
        if datasets is None:
            datasets = self.list_datasets()
        cats: set[str] = set()
        for dataset in datasets:
            try:
                collection = self.client.get_collection(self._collection_name(dataset))
            except Exception:
                continue
            metadata = getattr(collection, "metadata", None) or {}
            raw = metadata.get("categories") or "[]"
            try:
                cats.update(json.loads(raw))
            except (json.JSONDecodeError, TypeError):
                pass
        return sorted(cats)

    def random_terms(
        self,
        datasets: list[str] | None = None,
        k: int = 5,
        category: str | None = None,
    ) -> list[str]:
        """Tire jusqu'à k termes distincts (optionnellement dans une catégorie)."""
        if datasets is None:
            datasets = self.list_datasets()
        terms: list[str] = []
        for dataset in datasets:
            try:
                collection = self.client.get_collection(self._collection_name(dataset))
            except Exception:
                continue
            if category:
                res = collection.get(
                    where={"category": {"$contains": category}}, include=["metadatas"]
                )
                metas = res.get("metadatas", [])
            else:
                ids = collection.get(include=[])["ids"]
                if not ids:
                    continue
                sample_ids = random.sample(ids, min(k, len(ids)))
                res = collection.get(ids=sample_ids, include=["metadatas"])
                metas = res.get("metadatas", [])
            for meta in metas:
                term = meta.get("term")
                if term and term not in terms:
                    terms.append(term)
        random.shuffle(terms)
        return terms[:k]

    @staticmethod
    def _split_categories(value: Any) -> list[str]:
        """Découpe une valeur 'a|b;c' en catégories distinctes non vides."""
        cats: list[str] = []
        seen: set[str] = set()
        for part in re.split(r"[|;]", _clean(value)):
            c = part.strip()
            if c and c not in seen:
                seen.add(c)
                cats.append(c)
        return cats

    @staticmethod
    def _load_csv(csv_path: Path) -> tuple[list[str], list[str], list[list[str]]]:
        """Lit un CSV et accepte les variantes term/tag et definition/description.

        La colonne `category` peut contenir plusieurs catégories séparées par `|`
        (ou `;`) : ex. `vêtement|traditionnel`.
        """
        terms: list[str] = []
        definitions: list[str] = []
        categories: list[list[str]] = []
        with csv_path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                term = _clean(row.get("term") or row.get("tag"))
                definition = _clean(
                    row.get("definition")
                    or row.get("description")
                    or row.get("description_propre")
                )
                cats = RagIndex._split_categories(row.get("category"))
                if term and definition:
                    terms.append(term)
                    definitions.append(definition)
                    categories.append(cats)
        return terms, definitions, categories

    def _drop_collection(self, name: str) -> None:
        try:
            self.client.delete_collection(name)
        except Exception:
            pass
