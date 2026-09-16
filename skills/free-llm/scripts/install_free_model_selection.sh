#!/bin/bash
# install_free_model_selection.sh — instala o sistema de seleção de modelos FREE
# em qualquer perfil do Hermes Agent.
#
# Uso:
#   bash install_free_model_selection.sh <profile>
#   bash install_free_model_selection.sh pesquisa
#
# O que faz:
#   1. Copia os scripts update_models.py e update_free_models.py
#   2. Cria os 3 cron jobs no perfil (update-free-models-14h, choose-best-free-llm, update-hermes-models)
#   3. Verifica se as API keys existem no .env do perfil
#   4. Opcionalmente roda a primeira seleção

set -euo pipefail

# ─── Argumentos ───────────────────────────────────────────────────────────────
PROFILE="${1:-}"
if [[ -z "$PROFILE" ]]; then
    echo "ERRO: informe o perfil"
    echo "Uso: bash install_free_model_selection.sh <profile>"
    exit 1
fi

HERMES_HOME="$HOME/.hermes/profiles/$PROFILE"
if [[ ! -d "$HERMES_HOME" ]]; then
    echo "ERRO: perfil '$PROFILE' não encontrado em $HERMES_HOME"
    echo "Perfis disponíveis:"
    ls -1 "$HOME/.hermes/profiles/" 2>/dev/null | head -10
    exit 1
fi

# ─── Diretórios ───────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_SCRIPTS="$SCRIPT_DIR"
DST_SCRIPTS="$HERMES_HOME/scripts"
DST_CRON="$HERMES_HOME/cron"

echo "=== Instalando free-model-selection no perfil: $PROFILE ==="
echo "  origem:  $SRC_SCRIPTS"
echo "  destino: $DST_SCRIPTS"

# ─── Copiar scripts ───────────────────────────────────────────────────────────
mkdir -p "$DST_SCRIPTS"
for script in update_free_models.py update_models.py choose_best_free_llm.py; do
    if [[ -f "$SRC_SCRIPTS/$script" ]]; then
        cp -v "$SRC_SCRIPTS/$script" "$DST_SCRIPTS/"
    else
        echo "  WARN: $script não encontrado em $SRC_SCRIPTS"
    fi
done

# ─── Verificar API keys ───────────────────────────────────────────────────────
echo ""
echo "=== Verificando API keys no .env do perfil ==="
ENV_PATH="$HERMES_HOME/.env"
if [[ -f "$ENV_PATH" ]]; then
    for key in NVIDIA_API_KEY NOUS_API_KEY CLOUDFLARE_API_TOKEN; do
        if grep -q "^${key}=" "$ENV_PATH" 2>/dev/null; then
            echo "  ✓ $key encontrado"
        else
            echo "  ✗ $key NÃO encontrado — adicione ao $ENV_PATH"
        fi
    done
else
    echo "  ✗ $ENV_PATH não encontrado — crie com as API keys"
fi

# ─── Criar cron jobs ──────────────────────────────────────────────────────────
echo ""
echo "=== Criando cron jobs ==="
mkdir -p "$DST_CRON/output"

# Jobs que serão criados (JSON array)
JOBS=$(cat <<'JSON'
[
  {
    "name": "update-free-models-14h",
    "prompt": "",
    "script": "update_free_models.py",
    "no_agent": true,
    "schedule": {"kind": "cron", "expr": "0 8,20 * * *"},
    "repeat": {"times": null},
    "enabled": true,
    "state": "scheduled",
    "deliver": "local",
    "failure_deliver": "",
    "failure_streak": 0
  },
  {
    "name": "choose-best-free-llm",
    "prompt": "",
    "script": "choose_best_free_llm.py",
    "no_agent": true,
    "schedule": {"kind": "cron", "expr": "0 2,14 * * *"},
    "repeat": {"times": null},
    "enabled": true,
    "state": "scheduled",
    "deliver": "local",
    "failure_deliver": "",
    "failure_streak": 0
  },
  {
    "name": "update-hermes-models",
    "prompt": "",
    "script": "update_models.py",
    "no_agent": true,
    "schedule": {"kind": "cron", "expr": "0 9,21 * * *"},
    "repeat": {"times": null},
    "enabled": true,
    "state": "scheduled",
    "deliver": "local",
    "failure_deliver": "",
    "failure_streak": 0
  }
]
JSON
)

# Ler jobs existentes (se houver)
JOBS_FILE="$DST_CRON/jobs.json"
if [[ -f "$JOBS_FILE" ]]; then
    EXISTING=$(cat "$JOBS_FILE")
    # Verificar se já existem jobs com os mesmos nomes
    for JOB_NAME in "update-free-models-14h" "choose-best-free-llm" "update-hermes-models"; do
        if echo "$EXISTING" | grep -q "\"$JOB_NAME\""; then
            echo "  ! Job '$JOB_NAME' já existe — não sobrescrevendo"
        fi
    done
    echo "  → Mantendo jobs existentes. Para recriar, apague manualmente:"
    echo "    rm $JOBS_FILE"
else
    echo "$JOBS" | python3 -c "
import json, sys, hashlib, time
data = json.load(sys.stdin)
for job in data:
    job['id'] = hashlib.md5(f\"{job['name']}{time.time()}\".encode()).hexdigest()[:12]
    job['created_at'] = time.strftime('%Y-%m-%dT%H:%M:%S-03:00')
    job['next_run_at'] = None
    job['last_run_at'] = None
    job['last_status'] = None
    job['last_error'] = None
    job['last_delivery_error'] = None
    job['last_delivery_unverified'] = None
    job['last_dispatch'] = None
    job['fire_claim'] = None
    job['skills'] = []
    job['skill'] = None
    job['model'] = None
    job['provider'] = None
    job['provider_snapshot'] = None
    job['model_snapshot'] = None
    job['base_url'] = None
    job['monitor_script'] = None
    job['monitor_url'] = None
    job['monitor_state'] = None
    job['context_from'] = None
    job['paused_at'] = None
    job['paused_reason'] = None
    job['origin'] = None
    job['enabled_toolsets'] = None
    job['workdir'] = None
    job['schedule_display'] = job['schedule']['expr']
    job['repeat']['completed'] = 0
    print(json.dumps(data, indent=2, ensure_ascii=False))
" > "$JOBS_FILE"
    echo "  ✓ 3 cron jobs criados em $JOBS_FILE"
fi

# ─── Resultado ────────────────────────────────────────────────────────────────
echo ""
echo "=== Instalação concluída ==="
echo ""
echo "Para verificar:"
echo "  hermes cron list --profile $PROFILE"
echo ""
echo "Para rodar agora:"
echo "  HERMES_HOME=$HERMES_HOME python3 $DST_SCRIPTS/update_free_models.py"
echo "  HERMES_HOME=$HERMES_HOME python3 $DST_SCRIPTS/update_models.py"
echo ""
echo "Para desinstalar:"
echo "  rm -f $DST_SCRIPTS/update_{free_,}models.py $DST_SCRIPTS/choose_best_free_llm.py"
echo "  rm -f $JOBS_FILE  # e recriar sem os jobs"
