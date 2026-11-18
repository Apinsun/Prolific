# Prolific

Génération d'images en masse, orchestrée par un LLM (Ollama) + ComfyUI.

- **Spec** : [`spec.md`](spec.md)
- **Config** : copier `config.yaml.example` vers `config.yaml` et l'adapter.
- **Workflows** : déposer les exports ComfyUI (format API) dans `workflows/` et les
  déclarer dans `workflows/manifest.json` (voir `workflows/manifest.example.json`).

## Prérequis

- Python 3.11+ et [Poetry](https://python-poetry.org/)
- ComfyUI lancé (par l'utilisateur) : `http://127.0.0.1:8188`
- Ollama lancé (par l'utilisateur) : `http://localhost:11434`
- Modèle d'embedding pour le RAG : `ollama pull nomic-embed-text`

## Installation & lancement

```bash
./scripts/install.sh   # poetry install
./scripts/start.sh     # démarre le service (API + UI + MCP)
./scripts/stop.sh      # arrête le service
```

## MCP

Le service expose un endpoint MCP en **Streamable HTTP** sur
`http://127.0.0.1:8000/mcp` (une fois `./scripts/start.sh` lancé).

Outils exposés : `create_job`, `get_job`, `list_jobs`, `stop_job`, `list_workflows`,
`list_datasets`, `search_terms`.

## Structure

```
src/prolific/     ← code applicatif
workflows/        ← workflows exportés + manifest.json (gitignoré, ex. versionné)
datasets/         ← CSVs (term, definition, category) indexés en ChromaDB
traces/           ← traces JSONL (gitignoré — jamais commité)
config.yaml       ← config locale (gitignorée, exemple versionné)
```

> ⚠️ `traces/` et `workflows/` sont **hors repo** (voir `.gitignore`).
