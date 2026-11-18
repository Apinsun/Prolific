"""Point d'entrée du service Prolific (API + UI + MCP)."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI

from prolific.clients.comfyui import ComfyUIClient
from prolific.clients.ollama import OllamaClient
from prolific.config import load_config
from prolific.db import Database
from prolific.mcp_server import build_mcp
from prolific.orchestrator import Orchestrator
from prolific.rag import RagIndex
from prolific.workflows import load_manifest

WORKFLOWS_DIR = Path("workflows")


def create_app() -> FastAPI:
    """Construit l'application : dépendances + FastAPI + MCP monté sur /mcp."""
    config = load_config()
    db = Database()
    ollama = OllamaClient(config.llm.base_url)
    comfyui = ComfyUIClient(config.comfyui.url)
    rag = RagIndex(Path(config.rag.chroma_dir), ollama, config.rag.embed_model)
    manifest = load_manifest(WORKFLOWS_DIR)

    orchestrator = Orchestrator(
        config=config,
        db=db,
        ollama=ollama,
        comfyui=comfyui,
        rag=rag,
        workflows_dir=WORKFLOWS_DIR,
        manifest=manifest,
    )

    mcp = build_mcp(orchestrator=orchestrator, db=db, rag=rag, manifest=manifest)
    mcp_app = mcp.http_app(path="/", transport="streamable-http")

    # le lifespan du MCP doit être transmis à FastAPI (sinon le gestionnaire de
    # sessions Streamable HTTP n'est pas initialisé).
    app = FastAPI(title="Prolific", version="0.1.0", lifespan=mcp_app.lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # MCP en Streamable HTTP, accessible sur http://<host>:<port>/mcp
    app.mount("/mcp", mcp_app)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("prolific.main:app", host="127.0.0.1", port=8000, reload=True)
