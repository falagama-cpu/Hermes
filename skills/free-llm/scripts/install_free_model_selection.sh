#!/usr/bin/env bash
# install_free_model_selection.sh — instala o seletor de LLMs FREE (v4 + ranking
# Artificial Analysis) em um perfil do Hermes Agent.
#
# Uso:
#   bash install_free_model_selection.sh <perfil> [deliver] [--check] [--yes]
#     <perfil>   nome do perfil (ex.: trabalho) ou "default" (= ~/.hermes)
#     [deliver]  destino do relatório do cron: local (padrão), telegram, telegram:<chat_id>
#     --check    só verifica dependências e chaves; não instala nada
#     --yes      não interativo: não pergunta chaves (aborta se faltar o mínimo)
#
# Ordem — nada é copiado nem agendado antes dos passos 1 e 2 passarem:
#   1. Dependências  hermes, perfil, python3 >= 3.9, PyYAML, systemctl --user
#   2. Chaves de API pede as ausentes (entrada oculta) e grava em <perfil>/.env (chmod 600)
#   3. Instalação    copia scripts para <perfil>/scripts/ e cria 1 job de cron
#   4. Teste         comando de simulação (--dry-run), não grava config

set -euo pipefail

usage() { sed -n 2,16p "$0"; }
PROFILE=""; DELIVER="local"; YES=0; CHECK=0
for a in "$@"; do
    case "$a" in
        --yes|-y)  YES=1 ;;
        --check)   CHECK=1 ;;
        -h|--help) usage; exit 0 ;;
        *) if [[ -z "$PROFILE" ]]; then PROFILE="$a"; else DELIVER="$a"; fi ;;
    esac
done
[[ -n "$PROFILE" ]] || { usage; exit 1; }

if [[ "$PROFILE" == "default" ]]; then
    PHOME="$HOME/.hermes"; HP=()
else
    PHOME="$HOME/.hermes/profiles/$PROFILE"; HP=(-p "$PROFILE")
fi
SRC="$(cd "$(dirname "$0")" && pwd)"
ENVF="$PHOME/.env"
FAIL=0

ok()   { echo "  ✓ $*"; }
warn() { echo "  ! $*"; }
bad()  { echo "  ✗ $*"; FAIL=1; }

# ---------------------------------------------------------------------------
echo "=== 1. Dependências ==="
if command -v hermes >/dev/null 2>&1; then
    ok "hermes: $(command -v hermes)"
else
    bad "hermes não encontrado no PATH. Instale o Hermes Agent primeiro:"
    echo "      curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash"
    echo "      docs: https://hermes-agent.nousresearch.com/docs"
fi

if [[ -d "$PHOME" ]]; then
    ok "perfil: $PHOME"
else
    bad "perfil '$PROFILE' não existe ($PHOME). Crie com: hermes profile create $PROFILE"
fi

if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))'; then
    ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
    if python3 -c 'import yaml' 2>/dev/null; then
        ok "PyYAML"
    else
        warn "PyYAML ausente no python3 do PATH."
        echo "      O cron roda no Python do Hermes (já inclui PyYAML); só a execução manual precisa."
        echo "      Instalar: sudo apt install python3-yaml   (ou: python3 -m pip install --user pyyaml)"
    fi
else
    bad "python3 >= 3.9 ausente. Instale: sudo apt install python3   (macOS: brew install python)"
fi

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    ok "systemctl --user (reinicia o gateway após trocar modelo)"
else
    warn "systemd --user indisponível: o seletor grava o config mas não reinicia o gateway."
    echo "      Reinicie manualmente (hermes gateway restart) ou defina HERMES_GATEWAY_UNIT."
fi
echo "  (requer HTTPS de saída: openrouter.ai, integrate.api.nvidia.com,"
echo "   inference-api.nousresearch.com, api.cloudflare.com, artificialanalysis.ai)"

if [[ $FAIL -ne 0 ]]; then
    echo ""; echo "ABORTADO: resolva as dependências marcadas com ✗ e rode de novo. Nada foi instalado."
    exit 1
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== 2. Chaves de API ($ENVF) ==="
# NOME|onde obter
KEYS=(
  "ARTIFICIAL_ANALYSIS_API_KEY|https://artificialanalysis.ai → API Access (grátis, 1000 req/dia; ranking por benchmark)"
  "OPENROUTER_API_KEY|https://openrouter.ai/settings/keys (modelos :free)"
  "NVIDIA_API_KEY|https://build.nvidia.com → Get API Key (nvapi-...)"
  "NOUS_API_KEY|https://portal.nousresearch.com → API Keys"
  "CLOUDFLARE_API_TOKEN|https://dash.cloudflare.com/profile/api-tokens (template 'Workers AI')"
  "CLOUDFLARE_ACCOUNT_ID|https://dash.cloudflare.com → Workers AI → Account ID"
)
PROVIDERS=(OPENROUTER_API_KEY NVIDIA_API_KEY NOUS_API_KEY CLOUDFLARE_API_TOKEN)

has_key() { [[ -f "$ENVF" ]] && grep -qE "^$1=.+" "$ENVF"; }

set_key() {  # grava/atualiza NOME=valor sem ecoar o valor
    local name="$1" val="$2"
    touch "$ENVF"; chmod 600 "$ENVF"
    if grep -qE "^$name=" "$ENVF"; then
        python3 - "$ENVF" "$name" "$val" <<'PY'
import sys, pathlib
p, n, v = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
p.write_text("".join(f"{n}={v}\n" if l.startswith(n + "=") else l
                     for l in p.read_text().splitlines(True)))
PY
    else
        printf '%s=%s\n' "$name" "$val" >> "$ENVF"
    fi
}

for entry in "${KEYS[@]}"; do
    IFS='|' read -r name url <<<"$entry"
    if has_key "$name"; then ok "$name"; continue; fi
    if [[ $YES -eq 1 || $CHECK -eq 1 || ! -t 0 ]]; then
        warn "$name ausente — obter em: $url"; continue
    fi
    echo "  ? $name ausente — obter em: $url"
    read -r -s -p "    Cole o valor (Enter = pular): " val; echo
    if [[ -n "${val// /}" ]]; then set_key "$name" "$val"; ok "$name gravada"; else warn "$name pulada"; fi
done

n_prov=0
for k in "${PROVIDERS[@]}"; do if has_key "$k"; then n_prov=$((n_prov + 1)); fi; done
if has_key CLOUDFLARE_API_TOKEN && ! has_key CLOUDFLARE_ACCOUNT_ID; then
    warn "CLOUDFLARE_API_TOKEN sem CLOUDFLARE_ACCOUNT_ID: a fonte Cloudflare será ignorada."
fi
has_key ARTIFICIAL_ANALYSIS_API_KEY || warn "sem ARTIFICIAL_ANALYSIS_API_KEY o ranking usa heurística por palavras-chave."
if [[ $n_prov -eq 0 ]]; then
    echo ""
    echo "ABORTADO: nenhuma chave de provedor (OpenRouter/NVIDIA/Nous/Cloudflare)."
    echo "Sem ao menos uma o seletor não encontra modelo algum. Nada foi instalado."
    exit 1
fi
ok "$n_prov/4 provedores com chave"

if [[ $CHECK -eq 1 ]]; then echo ""; echo "--check: pré-requisitos OK. Nada instalado."; exit 0; fi

# ---------------------------------------------------------------------------
DST="$PHOME/scripts"
echo ""
echo "=== 3. Instalação em $DST ==="
mkdir -p "$DST"
for f in hermes-free-model-selector-v4.py run_model_selector_v4.py aa_scores.py; do
    cp -v "$SRC/$f" "$DST/"
done
python3 -m py_compile "$DST/hermes-free-model-selector-v4.py" "$DST/run_model_selector_v4.py" "$DST/aa_scores.py"
ok "scripts compilam"

if hermes "${HP[@]}" cron list 2>/dev/null | grep -q "choose-best-free-llm"; then
    warn "job choose-best-free-llm já existe — não recriado (ajuste: hermes ${HP[*]} cron edit <id>)"
else
    hermes "${HP[@]}" cron create "0 2,8,14,20 * * *" \
        --name choose-best-free-llm --script run_model_selector_v4.py --no-agent \
        --deliver "$DELIVER"
    ok "job choose-best-free-llm (02/08/14/20h, deliver=$DELIVER)"
fi
if hermes "${HP[@]}" cron list 2>/dev/null | grep -qE "update-hermes-models|update-free-models"; then
    warn "job legado encontrado — pause-o (dois jobs gravando model.default conflitam):"
    echo "      hermes ${HP[*]} cron list ; hermes ${HP[*]} cron pause <job_id>"
fi

# ---------------------------------------------------------------------------
echo ""
echo "=== 4. Teste (simulação, não grava config) ==="
echo "  cd $DST && HERMES_HOME=$PHOME python3 run_model_selector_v4.py --dry-run --force --no-restart"
