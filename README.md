# Hermes — skills públicas (falagama-cpu)

## free-llm — seleção automática de LLMs gratuitas

Um job de cron escolhe o modelo principal, o agregador MoA e 3 fallbacks **somente entre
modelos gratuitos** (OpenRouter, NVIDIA NIM, Nous Portal, Cloudflare Workers AI), ranqueados
pelos benchmarks da [Artificial Analysis](https://artificialanalysis.ai), e grava no
`config.yaml` do perfil do Hermes. Documentação completa: [`skills/free-llm/SKILL.md`](skills/free-llm/SKILL.md).

### 1. Pré-requisitos (o instalador verifica e avisa)

| Dependência | Obrigatória | Como instalar |
|---|---|---|
| [Hermes Agent](https://hermes-agent.nousresearch.com/docs) + 1 perfil | sim | `curl -fsSL https://hermes-agent.nousresearch.com/install.sh \| bash` · `hermes profile create <perfil>` |
| `python3` ≥ 3.9 | sim | `sudo apt install python3` (macOS: `brew install python`) |
| PyYAML no `python3` do PATH | só p/ execução manual (o cron usa o Python do Hermes) | `sudo apt install python3-yaml` ou `python3 -m pip install --user pyyaml` |
| `systemctl --user` | não (sem ele o gateway não é reiniciado sozinho) | Linux com systemd |
| HTTPS de saída | sim | openrouter.ai, integrate.api.nvidia.com, inference-api.nousresearch.com, api.cloudflare.com, artificialanalysis.ai |

Nenhum pacote pip extra: os scripts usam só stdlib + PyYAML.

### 2. Chaves de API (pedidas ANTES de instalar)

Gravadas em `~/.hermes/profiles/<perfil>/.env` (chmod 600). Exige **pelo menos um** provedor;
quanto mais, maior o pool de modelos.

| Variável | Onde obter |
|---|---|
| `ARTIFICIAL_ANALYSIS_API_KEY` | https://artificialanalysis.ai → API Access (grátis, 1000 req/dia). Sem ela o ranking cai em heurística. |
| `OPENROUTER_API_KEY` | https://openrouter.ai/settings/keys |
| `NVIDIA_API_KEY` | https://build.nvidia.com → Get API Key |
| `NOUS_API_KEY` | https://portal.nousresearch.com → API Keys |
| `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | https://dash.cloudflare.com/profile/api-tokens (template "Workers AI") · Account ID no painel Workers AI |

### 3. Instalação em um ambiente novo

```bash
git clone https://github.com/falagama-cpu/Hermes.git ~/Hermes
cd ~/Hermes/skills/free-llm/scripts

# (opcional) só verifica dependências e chaves, não instala nada
bash install_free_model_selection.sh <perfil> --check

# instala: verifica dependências → pede as chaves ausentes (entrada oculta)
#          → copia scripts p/ <perfil>/scripts/ → cria o job de cron
bash install_free_model_selection.sh <perfil> [deliver]
#   <perfil>  = nome do perfil, ou "default" para ~/.hermes
#   [deliver] = local (padrão) | telegram | telegram:<chat_id>  (para receber o relatório)
```

O instalador **aborta sem copiar nada** se faltar dependência obrigatória ou se nenhuma chave
de provedor for informada. Para registrar também a skill no agente:

```bash
mkdir -p ~/.hermes/profiles/<perfil>/skills/data-science
cp -r ~/Hermes/skills/free-llm ~/.hermes/profiles/<perfil>/skills/data-science/
```

### 4. Verificar

```bash
hermes -p <perfil> cron list
cd ~/.hermes/profiles/<perfil>/scripts
HERMES_HOME=~/.hermes/profiles/<perfil> python3 run_model_selector_v4.py --dry-run --force --no-restart
```

### Desinstalar

```bash
hermes -p <perfil> cron list            # pegue o id de choose-best-free-llm
hermes -p <perfil> cron remove <id>
rm -f ~/.hermes/profiles/<perfil>/scripts/{hermes-free-model-selector-v4.py,run_model_selector_v4.py,aa_scores.py}
rm -rf ~/.hermes/profiles/<perfil>/model-selector
```

Licença MIT. Dados de ranking: Artificial Analysis (atribuição exigida pelos termos).
