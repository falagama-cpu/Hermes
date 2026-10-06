#!/usr/bin/env bash
# council.sh - Simple wrapper to run Hermes Council skill
# Uso: HERMES_HOME=~/.hermes/profiles/<perfil> ./council.sh "pergunta" [--members N]

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Use uv to run the council script with all passed arguments
uv run python council.py "$@"