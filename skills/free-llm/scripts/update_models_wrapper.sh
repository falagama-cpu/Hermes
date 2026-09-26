#!/bin/bash
# Wrapper for update_models.py that attempts to restart gateway after successful update

SCRIPT_DIR="/home/fabio/.hermes/profiles/pesquisa/scripts"
cd "$SCRIPT_DIR" || exit 1

# Garante que pacotes locais (ex: requests/) em scripts/ estejam disponíveis
export PYTHONPATH="$SCRIPT_DIR:${PYTHONPATH:-}"

# Run the model update script
python3 update_models.py
UPDATE_EXIT_CODE=$?

# If update succeeded, attempt to restart gateway
if [ $UPDATE_EXIT_CODE -eq 0 ]; then
    echo "[$(date)] Update successful, attempting gateway restart..." >&2
    # O gateway roda como host gateway (hermes-gateway.service), não por perfil.
    # systemctl --user restart é o único jeito confiável de dentro do cron.
    systemctl --user restart hermes-gateway.service 2>&1 || \
        HERMES_HOME="$HOME/.hermes" hermes gateway restart 2>&1 || \
        echo "[$(date)] gateway restart falhou (inofensivo se já está rodando)" >&2
else
    echo "[$(date)] Update failed (exit code $UPDATE_EXIT_CODE), skipping gateway restart" >&2
fi

exit $UPDATE_EXIT_CODE