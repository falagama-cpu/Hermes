---
name: free-llm
description: "Use when tuning, installing or publishing the free-LLM selector."
version: 2.3.0
author: falagama-cpu
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [llm, free-models, cron, artificial-analysis, openrouter, nvidia, cloudflare, nous, gemini]
    related_skills: [hermes-agent]
prerequisites:
  commands: [hermes]
required_environment_variables:
  - name: ARTIFICIAL_ANALYSIS_API_KEY
    prompt: Artificial Analysis API key (ranking por benchmark)
    help: "Grátis (1000 req/dia): https://artificialanalysis.ai → API Access"
    required_for: ranking por benchmark; sem ela usa heurística
    optional: true
  - name: OPENROUTER_API_KEY
    prompt: OpenRouter API key
    help: https://openrouter.ai/settings/keys
    optional: true
  - name: NVIDIA_API_KEY
    prompt: NVIDIA NIM API key
    help: https://build.nvidia.com → Get API Key
    optional: true
  - name: NOUS_API_KEY
    prompt: Nous Portal API key
    help: https://portal.nousresearch.com → API Keys
    optional: true
  - name: CLOUDFLARE_API_TOKEN
    prompt: Cloudflare API token (Workers AI)
    help: https://dash.cloudflare.com/profile/api-tokens (template Workers AI)
    optional: true
  - name: CLOUDFLARE_ACCOUNT_ID
    prompt: Cloudflare Account ID
    help: https://dash.cloudflare.com → Workers AI → Account ID
    optional: true
  - name: GOOGLE_API_KEY
    prompt: Google AI Studio API key (Gemini)
    help: https://aistudio.google.com/apikey
    optional: true
---

# Free LLM Selection (seletor v4 + Artificial Analysis)

## When to Use

- O modelo principal/fallbacks do perfil "não troca", trocou para algo ruim, ou o relatório do cron não chega.
- Ajustar ranking, pesos, provedores ou a lista de modelos gratuitos.
- Instalar a seleção automática em outro perfil.
- Investigar 403/404/timeouts de provedores free (Cloudflare, NVIDIA NIM, OpenRouter, Nous).

Um único job de cron escolhe o modelo principal, o agregador MoA e 3 fallbacks **somente entre modelos gratuitos** e grava no `config.yaml` do perfil. O ranking usa os benchmarks medidos da Artificial Analysis (AA); a AA nunca adiciona modelos, só ordena os que já passaram no filtro free.

## Instalação em ambiente novo

Ordem obrigatória — o instalador não copia nem agenda nada antes de 1 e 2 passarem:

1. **Dependências**: `hermes` no PATH + perfil existente, Python ≥ 3.9, PyYAML (só p/ execução manual; o cron usa o Python do Hermes), `systemctl --user` (opcional, só Linux: sem ele o gateway não é reiniciado — ele relê `config.yaml` a cada mensagem), HTTPS de saída para os 5 domínios. Faltando algo obrigatório → aborta com o comando de instalação.
2. **Chaves**: pede as ausentes com entrada oculta (`getpass`) e grava em `<perfil>/.env` (chmod 600 em POSIX). Exige ≥ 1 provedor (OpenRouter/NVIDIA/Nous/Cloudflare); sem nenhum → aborta.
3. **Instalação**: copia os 3 scripts para `<perfil>/scripts/` e cria **um** job `choose-best-free-llm` (02/08/14/20h, `--no-agent`).

Instalador único em Python (Linux, macOS, Windows) — guia passo a passo em [`INSTALL.md`](../../INSTALL.md):

```bash
python3 install.py <perfil> --check           # só verifica   (Windows: py install.py ...)
python3 install.py <perfil> telegram          # instala; perfil "default" = home do Hermes
```

Home do Hermes: Linux/macOS `~/.hermes`, Windows `%LOCALAPPDATA%\hermes`; perfis em `<home>/profiles/<nome>`.

Ao instalar via agente: rode `--check` primeiro e, se faltar dependência ou chave, **avise o usuário e peça antes de prosseguir** — nunca escreva chaves no chat; o usuário as digita no prompt oculto do instalador (ou via captura segura de segredos do Hermes declarada no frontmatter).

## Topologia recomendada

| Job | Horário | Script |
|---|---|---|
| `choose-best-free-llm` | 02/08/14/20h | `run_model_selector_v4.py` |

**Nunca deixe outro script gravando `model.default`** (ex.: scripts antigos de seleção de modelo): com dois escritores o modelo depende de quem rodou por último e parece "não trocar" — ou ALTERNA a cada poucas horas (sintoma: dois jobs gravando modelos diferentes em horários alternados). O v4 é o **único escritor** de MAIN, `moa.aggregator`, `moa.reference_models` e `fallback_providers`; o instalador avisa se encontrar job legado. Diagnóstico: compare `config.yaml` × `state.json` e a origem do último backup em `backups/config/`.

Gateway (Linux/systemd): em setup multiplex (um `hermes-gateway.service` servindo vários perfis) o seletor reinicia o host; só usa `hermes-gateway-<perfil>.service` se estiver **ativo**. Override: env `HERMES_GATEWAY_UNIT`; `HERMES_GATEWAY_RESTART=0` desliga o restart. Reiniciar unit standalone desabilitado causa loop (exit 75, `Restart=always`). Windows/macOS: sem restart automático — o gateway relê `config.yaml` a cada mensagem; sessões abertas pegam o modelo novo com `/new`.

## Arquivos

Scripts (o cron roda a cópia em `<perfil>/scripts/`; a da skill é a fonte p/ instalar — mantenha as duas iguais):
- `hermes-free-model-selector-v4.py` — catálogo → filtro free → score → probe → gravação → warm-up → restart
- `aa_scores.py` — busca/cache da AA, matching nome↔id, score por papel
- `run_model_selector_v4.py` — wrapper de cron: roda o seletor em subprocesso e imprime **só o relatório curto**
- `install.py <perfil> [deliver] [--check] [--yes]` — dependências → chaves → scripts + 1 job (multiplataforma)
- `measure_source_overlap.py` — mede o ganho líquido de uma fonte de benchmark sobre o pool free

Estado em `<perfil>/model-selector/`: `catalog.json` (pool free com campo `aa`), `state.json` (última seleção + warm-up), `history.jsonl`, `selector.log`, `last_run.log` (log completo da última execução), `aa_cache.json`, `reliability.json`, `not_free.json`. Backups do config em `<perfil>/backups/config/config.yaml.bak.<ts>`.

## Chaves (`<perfil>/.env`)

`ARTIFICIAL_ANALYSIS_API_KEY` (grátis, 1000 req/dia, artificialanalysis.ai → API Access), `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `NOUS_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `GOOGLE_API_KEY` (aceita `GEMINI_API_KEY`; valores < 20 caracteres são ignorados como placeholder). Sem a chave da AA o seletor cai na heurística por palavras-chave (cron não quebra; relatório mostra `INATIVO`).

## Pipeline do v4

1. **Catálogo free** das 5 fontes (`:free`/pricing 0/allowlist NVIDIA/Cloudflare/Gemini da chave free tier), descarta embed/tts/imagem/vídeo e contexto < 64K. HTTP com backoff+jitter (429/5xx, 4 tentativas).
2. **Exclusões antes do score**: `not_free.json` (recusados por plano, 7 dias) e quarentena de `reliability.json` (≥3 falhas seguidas).
3. **Anotação AA** (`aa_scores.annotate`) e gravação do `catalog.json`.
4. **Score por papel** (`score_role`): com benchmark = 0.85×score AA + 0.15×heurística; sem benchmark e AA ativa = heurística − 10; menos penalidade de latência/falhas. Pesos AA: main 40% coding + 35% agentic + 25% intel; moa 60% intel + 20/20; reasoning 80% intel; long_context 50% contexto + 50% intel.
5. **Seleção** sem repetir família (dedupe cross-provider ignorando `:free`/provider): MAIN, MOA (aggregator), **MOA_REF1..N** (`moa.reference_models`, `HERMES_MOA_REFERENCE_COUNT`=2; papel moa, famílias ≠ MAIN/aggregator, +8 p/ fabricante inédito — podem coincidir com fallbacks, papéis distintos). **1 agente por provider**: aggregator e reference models só saem de providers ainda não usados por MAIN/MOA/refs (chamadas paralelas no mesmo provider estouram rate limit); repete provider só se não houver candidato em outro, F1 coding / F2 reasoning / F3 long-context.
6. **Probe antes de gravar** (`POST /chat/completions`, 15s): ∉ {200,429} → descarta e re-seleciona. Cloudflare 403 com `code 5035`/"Workers Free plan" → `mark_not_free()`. **Google**: probe em `/v1beta/openai/chat/completions` (30s); 429 conta como falha (cota diária), 429 `limit: 0` → `mark_not_free()`; cota gasta não soma rumo à quarentena; warm-up reaproveita o probe da execução (1 req/modelo/execução).
7. **Gravação**: `model.default/provider/base_url`, `moa.aggregator` + `moa.presets.default.aggregator`, `moa.reference_models` + `moa.presets.default.reference_models` (só se houver refs válidos; senão preserva), `auxiliary.moa_*`, `fallback_providers`. Nous e Cloudflare como `provider: custom` + `key_env` (nunca `custom:nome`). Backup + escrita atômica.
8. **Warm-up + restart** `--no-block` do gateway (em `--cron-mode` o warm-up vem antes, para não matar o próprio ticker).

## Relatório do cron

`no_agent` entrega o stdout; `deliver: local` = usuário não recebe nada. O wrapper imprime:
```
🤖 Seletor LLM FREE — 🔄 TROCOU modelos | ✅ OK — sem mudança | ❌ FALHOU | 🧪 SIMULAÇÃO (Ns)
MAIN: antigo → novo   (MOA, FB1..FB3 idem)
Warm-up: ✅ MAIN: HTTP 200 2204ms ...
Ranking: Artificial Analysis: ATIVO | origem=api|cache|cache-velho | N/M modelos free com benchmark
Avisos/erros: ...
```
Exit ≠ 0 (falha, timeout 1500s, ou `config.yaml` ≠ `state.json`) → o cron entrega alerta de falha.

## Comandos

```bash
# Linux/macOS
P=~/.hermes/profiles/<perfil>
cd $P/scripts && HERMES_HOME=$P python3 run_model_selector_v4.py --dry-run --force --no-restart   # simulação
cd $P/scripts && HERMES_HOME=$P python3 run_model_selector_v4.py                                  # execução real
tail -3 $P/model-selector/history.jsonl; less $P/model-selector/last_run.log
```

```powershell
# Windows (PowerShell)
$P = "$env:LOCALAPPDATA\hermes\profiles\<perfil>"
cd "$P\scripts"; $env:HERMES_HOME = $P; py run_model_selector_v4.py --dry-run --force --no-restart
Get-Content "$P\model-selector\history.jsonl" -Tail 3
```
Sem `HERMES_HOME` o seletor usa o perfil dono do diretório `scripts/` onde está instalado (fallback: home padrão do Hermes). Não rode `hermes cron run` de dentro de um chat do gateway (Telegram etc.): o restart do gateway derruba a conversa.

## Workflow de alteração

1. Backup com timestamp do script do **perfil**; edite; `python3 -m py_compile`.
2. Rode a simulação e leia o relatório + `last_run.log` (`probe ... HTTP <código>`).
3. Copie os scripts alterados para `<skill>/scripts/`. Se publicar a skill num repositório público, rode antes o gate de sanitização — ver `references/public-publishing.md`.
4. Mudou o instalador? Teste-o em HOME temporário conforme `references/public-publishing.md` antes de publicar.
5. Sincronize as 3 cópias (repo, skill local, `<perfil>/scripts/` com `.bak-<ts>`) e confira com `diff -rq --exclude __pycache__`.
6. Confira a próxima execução no destino do relatório ou em `<perfil>/cron/output/<job-id>/`.

## Fontes de score (benchmarks)

A AA é a única fonte de score do seletor. Antes de adicionar outra (leaderboard, dataset,
eval externa), siga `references/benchmark-sources.md`:

1. **Frescor primeiro** — confira a data do próprio dataset (`lastModified` na API do HF,
   ou o máximo da coluna de submissão). Fonte congelada não entra.
2. **Meça a sobreposição** com a AA pelo mesmo matching de tokens (`scripts/measure_source_overlap.py`)
   e decida pelo **ganho líquido** — modelos que só a nova fonte cobre — não pela contagem bruta.
3. **Semântica** — `score_role` usa índices por papel (coding/agentic/intel); média genérica
   de benchmark não mapeia para papel nenhum.

## Armadilhas

- **"O modelo não troca"**: primeiro confira `history.jsonl` — o v4 é determinístico, mesmo catálogo = mesmo MAIN. Depois confira se outro job grava `model.default`.
- **AA — índice agentic vem `null` em todos os modelos no tier free**: o código usa o intelligence index no lugar. Endpoint `GET https://artificialanalysis.ai/api/v2/data/llms/models`, header `x-api-key`, ~688 modelos. Cache 24h; API fora → cache até 30 dias. Atribuição à AA é exigida pelos termos.
- **Matching AA**: nomes vêm em outra ordem (`Llama 3.3 Instruct 70B` vs `llama-3.3-70b-instruct-fp8-fast`) → além do slug compacto há chave por conjunto de tokens sem ruído (instruct/fp8/fast/reasoning/max/high…) e remoção do prefixo do criador (`nvidia-nemotron-…`). `flash`/`mini`/`lite` **não** são ruído (outros modelos). Várias variantes AA → fica a de maior índice. Cobertura típica: ~55-65% do pool free tem benchmark AA.
- **Google/Gemini — free tier minúsculo**: ~20 req/dia **por modelo**. Pode virar MAIN pela nota da AA; a troca em tempo de execução é do próprio Hermes (`agent/error_classifier.py`): 429/402 → fallback imediato + cooldown do primário até o reset informado ("retry in 8h…"); 401/403 → fallback; 404/410/4xx desconhecido → `format_error`/`model_not_found` → fallback. Por isso `fallback_providers` nunca pode ficar vazio. Entrada gravada: `provider: gemini`, `base_url .../v1beta`, `key_env: GOOGLE_API_KEY` (evita o pool rotacionar para um `GEMINI_API_KEY` placeholder). Aliases `-latest` e modelos tts/image/omni/live são descartados.
- **Cloudflare 403 não é token**: `code 5035 "not available on the Workers Free plan"` = modelo pago para o plano da conta. O dashboard Workers AI lista-os sem indicar plano — só o probe revela. 5016/5018 = acesso restrito/formulário; 5006 = só aceita imagem.
- **NVIDIA sem allowlist (descoberta dinâmica, padrão)**: `/models` lista ~80 IDs sem preço; `nvidia_discover_free()` sonda TODOS (10 threads, ~25s) e só entra quem responde 200/429 — ~55 dão 404 (não-free). Cache em `model-selector/nvidia_free_probe.json` (OK 24h, 4xx 7d). Timeout/5xx mantém o modelo se teve OK < 7d ou está na seed `DEFAULT_NVIDIA_FREE_MODELS` (a seed agora é só rede de segurança). `NVIDIA_FREE_MODELS` no env = allowlist rígida; `HERMES_NVIDIA_DISCOVERY=0` = modo antigo. OpenRouter/Nous (pricing 0) e Cloudflare (`/ai/models/search`) já eram dinâmicos.
- **Metadados esparsos** (NVIDIA/Cloudflare sem description/contexto): `infer_sparse_metadata` + `MODEL_ID_HINTS`. Com a AA ativa isso pesa só 15%, mas cubra IDs novos para o fallback heurístico.
- **Confiabilidade**: `reliability.json` por `source::family` — EMA de latência (8s–45s desconta até 25 pts) + falhas (12 pts cada, teto 2); ≥3 falhas seguidas = quarentena.
- **Gateway**: o host relê `config.yaml` a cada mensagem; sessão com `/model` fixado mantém o modelo antigo. Linux: restart sempre `systemctl --user --no-block` (sem `--no-block` estoura timeout e o cron marca falha).
- **Todos os crons falham juntos com `cron external worker exited before ownership acknowledgement`**: regressão de update do Hermes (traceback de import no worker), não dos scripts — `hermes update` de novo.
- **Gateway standalone `failed` em setup multiplex é ruído** (Linux): confirme em `cron/executions.db` antes de culpar o gateway.
- **Windows — acentos/emojis**: os scripts forçam UTF-8 no stdout e em todo I/O de arquivo (`encoding="utf-8"`); o console cp1252 não quebra o relatório.
- **Valores do `.env` nunca viram constante de módulo**: `load_dotenv()` roda dentro de `run()`, depois do import — `os.environ.get("X", <default fixo>)` no topo do arquivo lê o default, não o `.env`. Guarde template (`.../accounts/{account}/ai/v1`) e formate no uso (`config_entry`).
- **Default de `HERMES_HOME` = perfil dono do script** (`Path(__file__).resolve().parent.parent`), nunca um nome de perfil fixo; o wrapper repassa `HERMES_HOME` explícito ao subprocesso para seletor e relatório lerem o mesmo perfil.

## Vision (auxiliar)

`auxiliary.vision.model` vazio quebra `vision_analyze`. Configure um VLM: `hermes config set auxiliary.vision.model meta/llama-3.2-11b-vision-instruct`.

## Referências

- `references/public-publishing.md` — gate de sanitização do repo público e receita de teste do instalador em HOME limpo
- `references/benchmark-sources.md` — candidatos a fonte de score (Artificial Analysis, Open LLM Leaderboard, Deepeval): frescor, cobertura medida e veredito
- https://artificialanalysis.ai/data-api/docs · https://openrouter.ai/docs · https://docs.nvidia.com/nim/ · https://developers.cloudflare.com/workers-ai/
