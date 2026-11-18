"""Point d'entrée du service Prolific (API + UI + MCP)."""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

app = FastAPI(title="Prolific", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("prolific.main:app", host="127.0.0.1", port=8000, reload=True)
