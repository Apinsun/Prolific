#!/usr/bin/env bash
set -e

# Arrêt propre du service (SIGTERM sur le process uvicorn).
if pkill -f "prolific.main" 2>/dev/null; then
    echo "✅ Prolific arrêté."
else
    echo "ℹ️  Prolific ne semble pas tourner."
fi
