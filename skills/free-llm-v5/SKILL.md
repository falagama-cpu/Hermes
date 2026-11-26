---
name: free-llm-v5
description: "Use when installing, running or auditing the free-LLM selector V5 (single file)."
version: 2.1.1
author: falagama-cpu
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [llm, free-models, cron, openrouter, nvidia, cloudflare, nous, gemini, openai-codex, chatgpt, v5]
    related_skills: [council]
prerequisites:
  commands: [hermes, python3]
required_environment_variables:
  - name: OPENROUTER_API_KEY
    prompt: OpenRouter API key
    help: https://openrouter.ai/settings/keys
    optional: true
  - name: NVIDIA_API_KEY
    prompt: NVIDIA NIM API key
    help: https://build.nvidia.com
    optional: true
  - name: NOUS_API_KEY
    prompt: Nous Portal API key
    help: https://portal.nousresearch.com
    optional: true
  - name: CLOUDFLARE_API_TOKEN
    prompt: Cloudflare API token (Workers AI)
    help: https://dash.cloudflare.com/profile/api-tokens
    optional: true
  - name: CLOUDFLARE_ACCOUNT_ID
    prompt: Cloudflare Account ID
    help: https://dash.cloudflare.com
    optional: true
  - name: GOOGLE_API_KEY
    prompt: Google AI Studio API key (Gemini)
    help: https://aistudio.google.com/apikey
    optional: true
---

# Free LLM Selection V5

Um job de cron escolhe **MAIN**, **MoA** (aggregator + reference models) e **3 fallbacks**
somente entre modelos gratuitos de OpenRouter, Nous Portal, Cloudflare Workers AI,
NVIDIA NIM, Google AI Studio e **OpenAI via login ChatGPT**, e grava no `config.yaml` do
perfil (com backup, probe e histórico).

**Arquivo único:** `scripts/hermes-free-model-selector-v5.py` contém a infraestrutura
operacional (ex-V4: fetchers, credenciais, warm-up, probe, persistência, backup,
confiabilidade, restart do gateway) e a seleção por papel. Não depende de nenhum
arquivo da V4 — substitui a skill `free-llm` (V4), removida em 2026-11-26.

**Seleção V5.2 (padrão):** score com benchmark Artificial Analysis + penalidade de
confiabilidade, família canônica entre provedores (a mesma LLM não ocupa dois papéis),
resultado determinístico (não depende da ordem do catálogo) e fallbacks fora do provedor
do MAIN/MOA. Rollback para a seleção V5.1 original: `HERMES_V5_SELECTION=5.1`.

## When to Use

- Instalar/rodar o seletor de LLMs gratuitas num perfil do Hermes.
- Diagnosticar por que uma fonte (ex.: ChatGPT) não entrou na seleção.
- Auditar a seleção (testes offline + golden de paridade).

## Files

- `scripts/hermes-free-model-selector-v5.py` — seletor (entrypoint único).
- `scripts/aa_scores.py`, `scripts/nvidia_official_free.py` — módulos de apoio (mesma pasta).
- `scripts/install.py` — instalador: verifica dependências e chaves, copia os scripts para
  `<perfil>/scripts/` e cria o job `choose-best-free-llm-v5` (avisa se o job V4 ainda existir).
- `scripts/measure_source_overlap.py` — mede a sobreposição de um dataset de benchmark com o pool free.
- `scripts/run_model_selector_v5.py` — wrapper de cron: roda o seletor, salva o log
  completo em `<HERMES_HOME>/model-selector/last_run_v5.log` e imprime só MAIN/MOA/fallbacks.
  Exit codes: `0` ok, `1` seletor não encontrado, `3` seleção não confirmada, `124` timeout.
- `tests/test_v5_unified.py` + `tests/fixtures/pool_20261126.json` — testes offline (31).
- `references/V5_TEST_AUDIT.md` — auditoria de testes, defeitos A1-A13 e correções V5.2.
- `references/V5_*.md` (demais) — documentação histórica do desenho "integrated".

## Uso

```bash
# instalar num perfil (Windows: py ...)
python3 scripts/install.py <perfil> --check
python3 scripts/install.py <perfil> [deliver]
# simular (não grava config.yaml)
HERMES_HOME=~/.hermes/profiles/<perfil> python3 scripts/hermes-free-model-selector-v5.py --dry-run
# cron (wrapper)
HERMES_HOME=~/.hermes/profiles/<perfil> python3 scripts/run_model_selector_v5.py
# testes
python3 tests/test_v5_unified.py
```

Agendamento sugerido (Hermes cron, `no_agent`): `0 2,10,18 * * *` (hora local).
Sempre defina `HERMES_HOME` explicitamente — o seletor reescreve o `config.yaml` do perfil
apontado (`model.default`, `moa`, `fallback_providers`) e reinicia o gateway. Sem ele o
padrão é `~/.hermes` (perfil default). `FREE_LLM_V5_SELECTOR` sobrescreve o caminho do seletor.

## Login ChatGPT (OpenAI Codex)

Sem API key: usa o login OAuth do Hermes (`hermes auth add openai-codex`).

- Ordem de busca do token: `auth.json` → `providers.openai-codex` → `credential_pool.openai-codex`
  → Codex CLI (`$CODEX_HOME/auth.json`, padrão `~/.codex/auth.json`).
- `auth.json` é lido como `utf-8-sig` (no Windows pode ter BOM; a V4 descartava o arquivo).
- `hermes auth status` diz "logged in" mesmo com o access token **vencido** (o Hermes renova
  na hora do uso). Se só houver token vencido, a V5 pede ao **próprio Hermes** para renovar
  (`hermes auth refresh openai-codex`, com o `HERMES_HOME` do perfil) e relê — o seletor nunca
  gasta o refresh token sozinho. Desligue com `HERMES_CODEX_REFRESH=0`.
- Disponibilidade vem de `/wham/usage` (não consome cota); cota esgotada → fonte ignorada na rodada.
- O log agora diz o **motivo** quando a fonte é ignorada (ausente, não-JWT, expirado, refresh falhou).

## Pitfalls

- **Nunca rode com `auth.json` copiado e refresh ligado**: a renovação rotaciona o refresh
  token na cópia e invalida o login original. Em sandbox/testes use `HERMES_CODEX_REFRESH=0`.
- Variáveis `*_BASE_URL` no ambiente sobrescrevem endpoints; um valor errado (ex.: uma API key
  em `NOUS_BASE_URL`) quebra o probe com `unknown url type`.
- Antes de mexer em pesos/bônus, meça com o fixture: 100 embaralhamentos devem dar
  1 seleção, 0 modelos repetidos entre papéis e 0 fallbacks no provedor do MAIN
  (`references/V5_TEST_AUDIT.md`, seção 5).

## Important

- `not_free` é momentâneo na V5 (`HERMES_NOT_FREE_TTL_S`, padrão 0).
- Nenhuma credencial é gravada na skill.
