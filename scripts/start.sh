#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."

if ! command -v poetry &> /dev/null; then
    echo "❌ Poetry n'est pas installé : https://python-poetry.org/"
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "📦 Installation des dépendances…"
    poetry install
fi

echo "🚀 Lancement de Prolific…"
exec poetry run python -m prolific.main
