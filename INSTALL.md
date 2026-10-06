# Guia de instalação

Instala o Hermes Agent, um perfil, a skill **free-llm** (seleção automática de LLMs
gratuitas) e a skill **council** (conselho de LLMs). Vale para Linux, macOS e Windows.

Convenções deste guia:

| | Linux / macOS | Windows |
|---|---|---|
| Terminal | bash/zsh | PowerShell |
| Python | `python3` | `py` (Python Launcher) ou `python` |
| Home do Hermes | `~/.hermes` | `%LOCALAPPDATA%\hermes` |
| Perfil `<perfil>` | `~/.hermes/profiles/<perfil>` | `%LOCALAPPDATA%\hermes\profiles\<perfil>` |

`<perfil>` é o nome que você escolher (ex.: `trabalho`). Use `default` para o perfil padrão.

---

## 1. Hermes Agent

```bash
# Linux / macOS / WSL2
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

```powershell
# Windows nativo (PowerShell)
iex (irm https://hermes-agent.nousresearch.com/install.ps1)
```

Também há pacote desktop para Windows (`.appinstaller`) e macOS (Apple Silicon) no site do
Hermes. Confira e crie o perfil:

```bash
hermes --version
hermes profile create <perfil>
hermes -p <perfil> setup        # provedor/modelo inicial e, se quiser, o gateway (Telegram etc.)
```

Documentação oficial: https://hermes-agent.nousresearch.com/docs

## 2. Baixar este repositório

```bash
git clone https://github.com/falagama-cpu/Hermes.git
cd Hermes
```

Sem git: baixe o ZIP em https://github.com/falagama-cpu/Hermes (Code → Download ZIP) e extraia.

## 3. Chaves de API

O seletor precisa de **pelo menos um** provedor; quanto mais, maior o pool de modelos.
Todas têm plano gratuito.

| Variável | Onde obter |
|---|---|
| `ARTIFICIAL_ANALYSIS_API_KEY` | https://artificialanalysis.ai → API Access (grátis, 1000 req/dia). Sem ela o ranking usa heurística. |
| `OPENROUTER_API_KEY` | https://openrouter.ai/settings/keys |
| `NVIDIA_API_KEY` | https://build.nvidia.com → Get API Key |
| `NOUS_API_KEY` | https://portal.nousresearch.com → API Keys |
| `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID` | https://dash.cloudflare.com/profile/api-tokens (template "Workers AI") · Account ID no painel Workers AI |

Você **não** precisa editar arquivos: o instalador pergunta cada chave ausente com entrada
oculta e grava no `.env` do perfil (permissão 600 no Linux/macOS). Regras de segurança:

- nunca cole chaves em chats, issues ou commits;
- o `.env` fica fora deste repositório, dentro do perfil Hermes;
- se uma chave vazar, **revogue-a** no painel do provedor e gere outra.

## 4. Instalar a skill free-llm

```bash
# Linux / macOS
python3 skills/free-llm/scripts/install.py <perfil> --check    # só verifica, não instala
python3 skills/free-llm/scripts/install.py <perfil> [deliver]
```

```powershell
# Windows
py skills\free-llm\scripts\install.py <perfil> --check
py skills\free-llm\scripts\install.py <perfil> [deliver]
```

`[deliver]` = para onde vai o relatório de cada execução: `local` (padrão, só fica salvo),
`telegram` (chat principal do gateway) ou `telegram:<chat_id>`. Precisa do gateway configurado
(`hermes -p <perfil> gateway setup`).

O instalador, nesta ordem:
1. verifica dependências (`hermes` no PATH, perfil existe, Python ≥ 3.9) — se faltar algo,
   **aborta sem copiar nada** e mostra como instalar;
2. pede as chaves ausentes (Enter pula) — sem nenhum provedor, aborta;
3. copia 3 scripts para `<perfil>/scripts/` e cria o job `choose-best-free-llm`
   (02h, 08h, 14h e 20h).

Opções: `--yes` (não interativo: não pergunta chaves), `--check`.

Para o agente também conhecer a skill (opcional):

```bash
# Linux / macOS
mkdir -p ~/.hermes/profiles/<perfil>/skills
cp -r skills/free-llm ~/.hermes/profiles/<perfil>/skills/
```

```powershell
# Windows
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\hermes\profiles\<perfil>\skills" | Out-Null
Copy-Item -Recurse skills\free-llm "$env:LOCALAPPDATA\hermes\profiles\<perfil>\skills\"
```

### Verificar

```bash
# Linux / macOS
hermes -p <perfil> cron list
cd ~/.hermes/profiles/<perfil>/scripts
HERMES_HOME=~/.hermes/profiles/<perfil> python3 run_model_selector_v4.py --dry-run --force --no-restart
```

```powershell
# Windows
hermes -p <perfil> cron list
cd "$env:LOCALAPPDATA\hermes\profiles\<perfil>\scripts"
$env:HERMES_HOME = "$env:LOCALAPPDATA\hermes\profiles\<perfil>"
py run_model_selector_v4.py --dry-run --force --no-restart
```

A simulação (`--dry-run`) mostra a seleção proposta e **não altera** o `config.yaml`.

### Como o modelo novo entra em uso

- O job grava `model.default`, `moa.*` e `fallback_providers` no `config.yaml` do perfil.
- O gateway relê o `config.yaml` a cada mensagem. Sessões abertas pegam o modelo novo com `/new`.
- Linux com systemd: o seletor também reinicia o gateway (`systemctl --user`). Windows/macOS:
  não há restart automático, e não é necessário. Para forçar: `hermes gateway restart`.
- Não deixe outro script gravando `model.default` (ex.: um seletor antigo): os dois brigam e o
  modelo fica alternando. Pause-o com `hermes -p <perfil> cron pause <id>`.

## 5. Instalar a skill council (opcional)

Requisitos: Python ≥ 3.10 e [uv](https://docs.astral.sh/uv/getting-started/installation/)
(ou `pip install httpx pyyaml`). Usa os mesmos modelos e chaves do perfil.

```bash
# Linux / macOS
cd skills/council
export COUNCIL_PROFILE=<perfil>
./council.sh "Qual a melhor estratégia para X?" --members 4
```

```powershell
# Windows
cd skills\council
$env:COUNCIL_PROFILE = "<perfil>"
.\council.ps1 "Qual a melhor estratégia para X?" --members 4
```

Se o PowerShell bloquear o script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
(ou rode direto: `uv run python council.py "pergunta"`).

Membros: MAIN (chairman) → aggregator do MoA → reference models do MoA → fallbacks
(sem repetir modelo). Ver os membros sem rodar:

```bash
uv run python -c "import council;[print(m.name,m.provider) for m in council.load_council_members(4)[0]]"
```

## 6. Atualizar

```bash
cd Hermes && git pull
python3 skills/free-llm/scripts/install.py <perfil>     # Windows: py ...
```

O instalador recopia os scripts e não duplica o job.

## 7. Desinstalar

```bash
hermes -p <perfil> cron list          # anote o id de choose-best-free-llm
hermes -p <perfil> cron remove <id>
```

Depois apague, dentro do perfil: `scripts/hermes-free-model-selector-v4.py`,
`scripts/run_model_selector_v4.py`, `scripts/aa_scores.py` e a pasta `model-selector/`.
O `config.yaml` fica com a última seleção; troque o modelo com `hermes -p <perfil> model`.

## Problemas comuns

| Sintoma | Causa / solução |
|---|---|
| `hermes não encontrado no PATH` | Abra um terminal novo após instalar o Hermes, ou adicione-o ao PATH. |
| `perfil '<perfil>' não existe` | `hermes profile create <perfil>` |
| `nenhuma chave de provedor` | Rode o instalador sem `--yes` e informe ao menos uma chave. |
| Modelo "não troca" | Confira se outro job grava `model.default`; sessões abertas precisam de `/new`. |
| Cloudflare 403 `code 5035` | O modelo não está no plano gratuito da conta; o seletor o exclui por 7 dias. |
| Relatório não chega | `deliver` = `local`, ou gateway não configurado (`hermes -p <perfil> gateway setup`). |
| Acentos estranhos no Windows | Use o Windows Terminal; os scripts já forçam UTF-8. |
