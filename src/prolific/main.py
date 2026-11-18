"""Point d'entrée du service Prolific (API + UI + MCP)."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from prolific.api import build_api
from prolific.clients.comfyui import ComfyUIClient
from prolific.clients.ollama import OllamaClient
from prolific.config import load_config
from prolific.db import Database
from prolific.logging_setup import setup_logging
from prolific.mcp_server import build_mcp
from prolific.orchestrator import Orchestrator
from prolific.rag import RagIndex
from prolific.workflows import load_manifest

WORKFLOWS_DIR = Path("workflows")
STATIC_DIR = Path("static")


def create_app() -> FastAPI:
    """Construit l'application : dépendances + FastAPI + UI + MCP monté sur /mcp."""
    config = load_config()
    setup_logging(config)
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

    api = build_api(
        config=config,
        db=db,
        ollama=ollama,
        rag=rag,
        manifest=manifest,
        orchestrator=orchestrator,
    )
    mcp = build_mcp(orchestrator=orchestrator, db=db, rag=rag, manifest=manifest)
    mcp_app = mcp.http_app(path="/", transport="streamable-http")

    # le lifespan du MCP doit être transmis à FastAPI (sinon le gestionnaire de
    # sessions Streamable HTTP n'est pas initialisé).
    app = FastAPI(title="Prolific", version="0.1.0", lifespan=mcp_app.lifespan)

    app.include_router(api, prefix="/api")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # MCP en Streamable HTTP, accessible sur http://<host>:<port>/mcp
    app.mount("/mcp", mcp_app)
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run("prolific.main:app", host="127.0.0.1", port=8000, reload=True)
