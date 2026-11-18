"""Client Ollama : génération, liste des modèles, déchargement VRAM."""

from __future__ import annotations

import subprocess
from typing import Any

import httpx


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434") -> None:
        self.base_url = base_url.rstrip("/")

    def list_models(self) -> list[dict[str, Any]]:
        with httpx.Client(timeout=15) as client:
            resp = client.get(f"{self.base_url}/api/tags")
            resp.raise_for_status()
            return resp.json().get("models", [])

    def generate(
        self,
        model: str,
        prompt: str,
        *,
        system: str | None = None,
        format: str | dict[str, Any] | None = None,  # noqa: A002 — miroir de l'API Ollama
        options: dict[str, Any] | None = None,
        timeout: float = 600,
    ) -> str:
        """Appel non-streamé à /api/generate. Retourne le texte produit."""
        payload: dict[str, Any] = {"model": model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system
        if format is not None:
            payload["format"] = format
        if options:
            payload["options"] = options

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{self.base_url}/api/generate", json=payload)
            resp.raise_for_status()
            return resp.json().get("response", "")

    def unload(self, model: str) -> None:
        """Décharge un modèle de la VRAM (via CLI `ollama stop`, comme le skill comfyui)."""
        subprocess.run(["ollama", "stop", model], capture_output=True, check=False)
