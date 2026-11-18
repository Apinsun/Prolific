"""Indexe tous les CSVs de datasets/ dans le RAG.

Usage : python -m prolific.index_datasets   (ou ./scripts/index_datasets.sh)
"""

from __future__ import annotations

from pathlib import Path

from prolific.clients.ollama import OllamaClient
from prolific.config import load_config
from prolific.rag import RagIndex


def main() -> None:
    config = load_config()
    rag = RagIndex(
        Path(config.rag.chroma_dir),
        OllamaClient(config.llm.base_url),
        config.rag.embed_model,
    )
    datasets = rag.index_all(Path(config.datasets.dir))
    if datasets:
        print(f"Datasets indexés : {', '.join(datasets)}")
    else:
        print("Aucun CSV trouvé dans", config.datasets.dir)


if __name__ == "__main__":
    main()
