#!/bin/bash
# install_free_model_selection.sh — instala o seletor de LLMs FREE (v4 + ranking
# Artificial Analysis) em um perfil do Hermes Agent.
#
# Uso:
#   bash install_free_model_selection.sh <profile> [deliver]
#   bash install_free_model_selection.sh pesquisa telegram:5559735768
#
# O que faz:
#   1. Copia o seletor v4, o wrapper de relatório e aa_scores.py para <perfil>/scripts/
#   2. Cria UM job de cron (choose-best-free-llm, 02/08/14/20h) via `hermes cron create`,
#      no_agent, entregando o relatório curto em [deliver] (default: local)
#   3. Verifica as chaves no .env do perfil
#
# Um único dono do model.default: NÃO cria o job legado update-hermes-models
# (update_models.py) — ele sobrescrevia a escolha do v4.

set -euo pipefail

PROFILE="${1:-}"
DELIVER="${2:-local}"
if [[ -z "$PROFILE" ]]; then
    echo "Uso: bash install_free_model_selection.sh <profile> [deliver]"
    exit 1
fi

HERMES_HOME="$HOME/.hermes/profiles/$PROFILE"
if [[ ! -d "$HERMES_HOME" ]]; then
    echo "ERRO: perfil '$PROFILE' não encontrado em $HERMES_HOME"
    ls -1 "$HOME/.hermes/profiles/" 2>/dev/null | head -10
    exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
DST="$HERMES_HOME/scripts"
mkdir -p "$DST"

echo "=== Copiando scripts para $DST ==="
for f in hermes-free-model-selector-v4.py run_model_selector_v4.py aa_scores.py; do
    cp -v "$SRC/$f" "$DST/"
done
python3 -m py_compile "$DST/hermes-free-model-selector-v4.py" "$DST/run_model_selector_v4.py" "$DST/aa_scores.py"
python3 -c "import yaml" 2>/dev/null || echo "  ✗ PyYAML ausente no python3 do sistema (o wrapper precisa)"

echo ""
echo "=== Chaves no $HERMES_HOME/.env ==="
for key in ARTIFICIAL_ANALYSIS_API_KEY OPENROUTER_API_KEY NVIDIA_API_KEY NOUS_API_KEY \
           CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID; do
    if grep -qE "^${key}=.+" "$HERMES_HOME/.env" 2>/dev/null; then
        echo "  ✓ $key"
    else
        echo "  ✗ $key ausente"
    fi
done
echo "  (sem ARTIFICIAL_ANALYSIS_API_KEY o ranking cai na heurística por palavras-chave)"

echo ""
echo "=== Cron ==="
if hermes -p "$PROFILE" cron list 2>/dev/null | grep -q "choose-best-free-llm"; then
    echo "  ! choose-best-free-llm já existe — não recriado (ajuste com hermes cron edit)"
else
    hermes -p "$PROFILE" cron create "0 2,8,14,20 * * *" \
        --name choose-best-free-llm --script run_model_selector_v4.py --no-agent \
        --deliver "$DELIVER"
fi
if hermes -p "$PROFILE" cron list 2>/dev/null | grep -q "update-hermes-models"; then
    echo "  ! job legado update-hermes-models encontrado: pause-o (conflita com o v4):"
    echo "    hermes -p $PROFILE cron pause <job_id>"
fi

echo ""
echo "=== Teste (simulação, não grava) ==="
echo "  cd $DST && HERMES_HOME=$HERMES_HOME python3 run_model_selector_v4.py --dry-run --force --no-restart"
