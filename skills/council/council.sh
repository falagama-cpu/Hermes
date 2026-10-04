#!/usr/bin/env bash
# council.sh - Simple wrapper to run Hermes Council skill
# Assumes this script is located in ~/.hermes/profiles/<profile>/skills/council/

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Use uv to run the council script with all passed arguments
uv run python council.py "$@"