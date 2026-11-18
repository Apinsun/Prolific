"""Configuration de Prolific (config.yaml), typée via pydantic."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_PATH = Path("config.yaml")


class LLMConfig(BaseModel):
    provider: str = "ollama"
    base_url: str = "http://localhost:11434"
    model: str = ""


class ComfyUIConfig(BaseModel):
    url: str = "http://127.0.0.1:8188"


class OutputConfig(BaseModel):
    dir: str = "./output"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000


class DatasetsConfig(BaseModel):
    dir: str = "./datasets"


class RagConfig(BaseModel):
    embed_model: str = "bge-m3"
    chroma_dir: str = "./chroma_db"


class Config(BaseModel):
    llm: LLMConfig = Field(default_factory=LLMConfig)
    comfyui: ComfyUIConfig = Field(default_factory=ComfyUIConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    datasets: DatasetsConfig = Field(default_factory=DatasetsConfig)
    rag: RagConfig = Field(default_factory=RagConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Charge config.yaml, avec valeurs par défaut si absent ou incomplet."""
    if not path.exists():
        return Config()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config(**data)
