---
name: free-llm-v5
description: "Use when installing, tuning or publishing the free-LLM selector V5 (V4 infrastructure + V5.1 selection)"
version: 1.0.0
author: falagama-cpu
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [llm, free-models, cron, openrouter, nvidia, cloudflare, nous, gemini, openai-codex, v5]
    related_skills: [free-llm]
prerequisites:
  commands: [hermes]
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

V5 is a separate skill that keeps the proven V4 operational infrastructure
(fetchers, credentials, warm-up, persistence, backup, reliability) and replaces
only the selection algorithms with the V5.1 role-based implementation.

## When to Use

- Trocar MAIN, MOA e fallbacks do perfil com o algoritmo V5.
- Auditar a cadeia de seleção V5 separada da V4.
- Publicar ou instalar a V5 sem alterar a skill V4.

## Files

- `scripts/hermes-free-model-selector-v5-integrated.py` — V5 integrated entrypoint.
- `scripts/hermes-free-model-selector-v5.1.py` — V5.1 selection algorithms.
- `scripts/hermes-free-model-selector-v4.py` — V4 operational infrastructure used by V5.
- `scripts/aa_scores.py`, `scripts/nvidia_official_free.py` — V4 dependencies.
- `scripts/run_model_selector_v5.py` — cron wrapper: runs the selector, saves the full
  log to `<HERMES_HOME>/model-selector/last_run_v5.log` and prints only MAIN/MOA/fallbacks.
  Exit codes: `0` ok, `1` selector not found, `3` selection not confirmed, `124` timeout.
- `tests/test_v5_simple.py` — offline sanity checks of the V5 scoring rules (no network).
- `references/` — architecture, mapping, contracts, tests, migration and benchmark.

## Cron

```bash
HERMES_HOME=~/.hermes/profiles/<perfil> python3 scripts/run_model_selector_v5.py
```

Sugestão de agendamento (Hermes cron, `no_agent`): `0 2,10,18 * * *` (hora local).
Sempre defina `HERMES_HOME` explicitamente — o seletor reescreve o `config.yaml` do
perfil apontado (`model.default`, `moa`, `fallback_providers`). Sem ele, o padrão é
`~/.hermes` (perfil default). `FREE_LLM_V5_SELECTOR` sobrescreve o caminho do seletor.

## Important

- A V4 permanece intacta como skill separada.
- A V5 não substitui a V4 automaticamente.
- As exclusões `not_free` são momentâneas na V5 (duram apenas a execução corrente).
- Nenhuma credencial é gravada na skill.
