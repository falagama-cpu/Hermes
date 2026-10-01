---
name: free-llm
description: "Use when tuning or debugging the FREE LLM auto-selection cron. Seletor v4 ranqueia modelos gratuitos pela Artificial Analysis."
version: 2.0.0
author: falagama-cpu
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [llm, free-models, cron, artificial-analysis, openrouter, nvidia, cloudflare, nous]
    related_skills: [hermes-agent]
---

# Free LLM Selection (seletor v4 + Artificial Analysis)

## When to Use

- O modelo principal/fallbacks do perfil "não troca", trocou para algo ruim, ou o relatório do cron não chega.
- Ajustar ranking, pesos, provedores ou a lista de modelos gratuitos.
- Instalar a seleção automática em outro perfil.
- Investigar 403/404/timeouts de provedores free (Cloudflare, NVIDIA NIM, OpenRouter, Nous).

Um único job de cron escolhe o modelo principal, o agregador MoA e 3 fallbacks **somente entre modelos gratuitos** e grava no `config.yaml` do perfil. O ranking usa os benchmarks medidos da Artificial Analysis (AA); a AA nunca adiciona modelos, só ordena os que já passaram no filtro free.

## Estado atual (perfil <perfil>)

| Job | ID | Horário | Script | Estado |
|---|---|---|---|---|
| `choose-best-free-llm` | <job_id> | 02/14h | `run_model_selector_v4.py` | ativo, deliver `telegram:<chat_id>` |
| `update-free-models-14h` | <job_id> | 08/20h | `run_model_selector_v4.py` | ativo, deliver `telegram:<chat_id>` |
| `update-hermes-models` | <job_id> | 09/21h | `update_models_wrapper.sh` | **pausado** (legado, ver `references/legacy-scripts.md`) |

Os dois jobs ativos rodam o mesmo seletor → na prática 4 execuções/dia (02/08/14/20h). **Nunca deixe outro script gravando `model.default`**: com dois escritores o modelo no dashboard depende de quem rodou por último e parece "não trocar".

Perfil <perfil> é servido pelo host `hermes-gateway.service` (multiplex). `hermes-gateway-<perfil>.service` fica **disabled** — reiniciá-lo causa loop (exit 75, `Restart=always`).

## Arquivos

Scripts (o cron roda a cópia em `~/.hermes/profiles/<perfil>/scripts/`; a da skill é a fonte p/ instalar — mantenha as duas iguais):
- `hermes-free-model-selector-v4.py` — catálogo → filtro free → score → probe → gravação → warm-up → restart
- `aa_scores.py` — busca/cache da AA, matching nome↔id, score por papel
- `run_model_selector_v4.py` — wrapper de cron: roda o seletor em subprocesso e imprime **só o relatório curto**
- `install_free_model_selection.sh <perfil> [deliver]` — instala scripts + 1 job

Estado em `<perfil>/model-selector/`: `catalog.json` (pool free com campo `aa`), `state.json` (última seleção + warm-up), `history.jsonl`, `selector.log`, `last_run.log` (log completo da última execução), `aa_cache.json`, `reliability.json`, `not_free.json`. Backups do config em `<perfil>/backups/config/config.yaml.bak.<ts>`.

## Chaves (`<perfil>/.env`)

`ARTIFICIAL_ANALYSIS_API_KEY` (grátis, 1000 req/dia, artificialanalysis.ai → API Access), `OPENROUTER_API_KEY`, `NVIDIA_API_KEY`, `NOUS_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`. Sem a chave da AA o seletor cai na heurística por palavras-chave (cron não quebra; relatório mostra `INATIVO`).

## Pipeline do v4

1. **Catálogo free** das 4 fontes (`:free`/pricing 0/allowlist NVIDIA/Cloudflare), descarta embed/tts/imagem/vídeo e contexto < 64K. HTTP com backoff+jitter (429/5xx, 4 tentativas).
2. **Exclusões antes do score**: `not_free.json` (recusados por plano, 7 dias) e quarentena de `reliability.json` (≥3 falhas seguidas).
3. **Anotação AA** (`aa_scores.annotate`) e gravação do `catalog.json`.
4. **Score por papel** (`score_role`): com benchmark = 0.85×score AA + 0.15×heurística; sem benchmark e AA ativa = heurística − 10; menos penalidade de latência/falhas. Pesos AA: main 40% coding + 35% agentic + 25% intel; moa 60% intel + 20/20; reasoning 80% intel; long_context 50% contexto + 50% intel.
5. **Seleção** sem repetir família (dedupe cross-provider ignorando `:free`/provider): MAIN, MOA, F1 coding / F2 reasoning / F3 long-context.
6. **Probe antes de gravar** (`POST /chat/completions`, 15s): ∉ {200,429} → descarta e re-seleciona. Cloudflare 403 com `code 5035`/"Workers Free plan" → `mark_not_free()`.
7. **Gravação**: `model.default/provider/base_url`, `moa.aggregator` + `moa.presets.default.aggregator`, `auxiliary.moa_*`, `fallback_providers`. Nous e Cloudflare como `provider: custom` + `key_env` (nunca `custom:nome`). Backup + escrita atômica.
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
P=~/.hermes/profiles/<perfil>
# Simulação (não grava config, não reinicia; mostra a seleção proposta)
cd $P/scripts && HERMES_HOME=$P python3 run_model_selector_v4.py --dry-run --force --no-restart
# Execução real fora do cron
cd $P/scripts && HERMES_HOME=$P python3 run_model_selector_v4.py
# Histórico / último log
tail -3 $P/model-selector/history.jsonl; less $P/model-selector/last_run.log
# Instalar em outro perfil
bash <skill>/scripts/install_free_model_selection.sh <perfil> telegram:<chat_id>
```
Sem `HERMES_HOME` o seletor usa `~/.hermes/profiles/<perfil>` — sempre exporte para outro perfil. Não rode `hermes cron run` de dentro do chat do Telegram: o restart do gateway derruba a conversa.

## Workflow de alteração

1. Backup com timestamp do script do **perfil**; edite; `python3 -m py_compile`.
2. Rode a simulação e leia o relatório + `last_run.log` (`probe ... HTTP <código>`).
3. Copie os scripts alterados para `<skill>/scripts/` e faça commit/push em `~/Hermes` (falagama-cpu/Hermes).
4. Confira a próxima execução agendada no Telegram ou em `cron/output/<job-id>/`.

## Armadilhas

- **"O modelo não troca"**: primeiro confira `history.jsonl` — o v4 é determinístico, mesmo catálogo = mesmo MAIN. Depois confira se outro job grava `model.default`.
- **AA — índice agentic vem `null` em todos os modelos no tier free**: o código usa o intelligence index no lugar. Endpoint `GET https://artificialanalysis.ai/api/v2/data/llms/models`, header `x-api-key`, ~688 modelos. Cache 24h; API fora → cache até 30 dias. Atribuição à AA é exigida pelos termos.
- **Matching AA**: nomes vêm em outra ordem (`Llama 3.3 Instruct 70B` vs `llama-3.3-70b-instruct-fp8-fast`) → além do slug compacto há chave por conjunto de tokens sem ruído (instruct/fp8/fast/reasoning/max/high…) e remoção do prefixo do criador (`nvidia-nemotron-…`). `flash`/`mini`/`lite` **não** são ruído (outros modelos). Várias variantes AA → fica a de maior índice. Cobertura real: ~32-39 de ~56-63 free; sem benchmark: poolside/laguna, longcat-2.5, apodex-mini, dots-3.
- **Cloudflare 403 não é token**: `code 5035 "not available on the Workers Free plan"` = modelo pago na conta (kimi-k2.6/k2.7-code, deepseek-v4-pro/flash, glm-5.2/5.3/5.3-flash). O dashboard `dash.cloudflare.com/<acct>/ai/models` lista-os sem indicar plano — só o probe revela. 5016/5018 = acesso restrito/formulário; 5006 = só aceita imagem.
- **Metadados esparsos** (NVIDIA/Cloudflare sem description/contexto): `infer_sparse_metadata` + `MODEL_ID_HINTS`. Com a AA ativa isso pesa só 15%, mas cubra IDs novos para o fallback heurístico.
- **Confiabilidade**: `reliability.json` por `source::family` — EMA de latência (8s–45s desconta até 25 pts) + falhas (12 pts cada, teto 2); ≥3 falhas seguidas = quarentena.
- **Gateway**: o host relê `config.yaml` a cada mensagem; sessão com `/model` fixado mantém o modelo antigo. Restart sempre `systemctl --user --no-block` (sem `--no-block` estoura timeout e o cron marca falha).
- **Todos os crons falham juntos com `cron external worker exited before ownership acknowledgement`**: regressão de update do Hermes (traceback de import no worker), não dos scripts — `hermes update` de novo.
- **Gateway standalone `failed` em setup multiplex é ruído**: confirme em `cron/executions.db` antes de culpar o gateway.

## Vision (auxiliar)

`auxiliary.vision.model` vazio quebra `vision_analyze`. Configure um VLM: `hermes config set auxiliary.vision.model meta/llama-3.2-11b-vision-instruct`.

## Referências

- `references/legacy-scripts.md` — `update_models.py`/`update_free_models.py` (pausados), regex YAML, MoA multimodal
- https://artificialanalysis.ai/data-api/docs · https://openrouter.ai/docs · https://docs.nvidia.com/nim/ · https://developers.cloudflare.com/workers-ai/
