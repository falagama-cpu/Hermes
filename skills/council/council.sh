#!/usr/bin/env bash
# council.sh — wrapper do Hermes Council (Linux/macOS)
# Uso: COUNCIL_PROFILE=<perfil> ./council.sh "pergunta" [--members N]
#      (ou HERMES_HOME=~/.hermes/profiles/<perfil>)
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
exec uv run python council.py "$@"
