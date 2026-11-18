"""Clients HTTP vers les services externes (Ollama, ComfyUI)."""

from .comfyui import ComfyUIClient
from .ollama import OllamaClient

__all__ = ["OllamaClient", "ComfyUIClient"]
