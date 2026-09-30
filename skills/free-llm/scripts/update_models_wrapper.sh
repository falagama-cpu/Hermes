#!/bin/bash
# Wrapper for update_models.py that attempts to restart gateway after successful update

SCRIPT_DIR="~/.hermes/profiles/<perfil>/scripts"
cd "$SCRIPT_DIR" || exit 1

# Garante que pacotes locais (ex: requests/) em scripts/ estejam disponíveis
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

# Run the model update script
python3 update_models.py
UPDATE_EXIT_CODE=$?

# If update succeeded, attempt to restart gateway
if [ $UPDATE_EXIT_CODE -eq 0 ]; then
    # Restart já é feito (--no-block) dentro de update_models.py; não duplicar aqui.
    echo "[$(date)] Update successful (o update_models.py reinicia o gateway só se o config mudou; veja o log acima)" >&2
else
    echo "[$(date)] Update failed (exit code $UPDATE_EXIT_CODE), skipping gateway restart" >&2
fi

exit $UPDATE_EXIT_CODE