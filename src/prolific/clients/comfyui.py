"""Client ComfyUI : soumission de workflow, suivi, extraction, libération VRAM."""

from __future__ import annotations

import time
from typing import Any

import httpx


class ComfyUIClient:
    def __init__(self, url: str = "http://127.0.0.1:8188") -> None:
        self.url = url.rstrip("/")

    def is_online(self) -> bool:
        try:
            with httpx.Client(timeout=5) as client:
                return client.get(f"{self.url}/queue").status_code == 200
        except httpx.HTTPError:
            return False

    def submit(self, workflow: dict[str, Any]) -> str:
        """Soumet un workflow (format API). Retourne le prompt_id."""
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{self.url}/prompt", json={"prompt": workflow})
            resp.raise_for_status()
            return resp.json()["prompt_id"]

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        with httpx.Client(timeout=30) as client:
            resp = client.get(f"{self.url}/history/{prompt_id}")
            resp.raise_for_status()
            return resp.json().get(prompt_id, {})

    def wait_for(
        self,
        prompt_id: str,
        *,
        timeout: float = 300,
        poll_interval: float = 1.0,
    ) -> dict[str, Any]:
        """Poll l'historique jusqu'à complétion ou erreur. Lève TimeoutError sinon."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            history = self.get_history(prompt_id)
            if history:
                status = history.get("status", {})
                if status.get("completed") or status.get("status_str") == "error":
                    return history
            time.sleep(poll_interval)
        raise TimeoutError(f"Génération ComfyUI en timeout ({prompt_id})")

    @staticmethod
    def extract_images(history: dict[str, Any]) -> list[dict[str, Any]]:
        """Récupère les images produites (filename/subfolder/type) depuis l'historique."""
        images: list[dict[str, Any]] = []
        for node_id, output in history.get("outputs", {}).items():
            for img in output.get("images", []):
                images.append({"node_id": node_id, **img})
        return images

    def free_vram(self) -> None:
        """Décharge les modèles ComfyUI de la VRAM (POST /free)."""
        with httpx.Client(timeout=30) as client:
            client.post(
                f"{self.url}/free",
                json={"unload_models": True, "free_memory": True},
            )
