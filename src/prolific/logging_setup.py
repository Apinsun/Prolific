"""Configuration du logging (console + fichier optionnel)."""

from __future__ import annotations

import logging

from prolific.config import Config

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s — %(message)s"


def setup_logging(config: Config) -> None:
    """Configure le logging racine à partir de la config (niveau + fichier optionnel)."""
    level = getattr(logging, config.logging.level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.logging.file:
        handlers.append(logging.FileHandler(config.logging.file, encoding="utf-8"))

    logging.basicConfig(level=level, format=_FORMAT, handlers=handlers, force=True)

    # baisse le bruit des dépendances tierces
    for noisy in ("httpx", "chromadb", "langchain_ollama", "langchain_core", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
