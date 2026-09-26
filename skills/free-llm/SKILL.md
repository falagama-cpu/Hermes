---
name: free-llm
description: "Sistema de seleção e troca automática de modelos LLM FREE via cron para qualquer perfil do Hermes Agent. Detecta modelos gratuitos no OpenRouter, NVIDIA NIM, Nous Portal e Cloudflare Workers AI. Atualiza config.yaml com fallback chain, e reinicia o gateway para assumir novos modelos."
---

# Free LLM Selection

Sistema automatizado de seleção e troca de modelos LLM gratuitos entre 4 provedores:
- **OpenRouter** — modelos `:free` ou pricing 0 via catálogo `/models`, validados por probe HTTP
- **NVIDIA NIM** — previews free identificados por probe HTTP (integrate.api.nvidia.com)
- **Nous Portal** — modelos com tag `:free` autenticados por Bearer key (inference-api.nousresearch.com)
- **Cloudflare Workers AI** — modelos free via API (api.cloudflare.com) com Bearer token

## Como funciona

1. O cron roda os scripts em `scripts/` que sondam os provedores via HTTP
2. Filtram modelos com preço zero ou tag `:free`
3. Montam uma fallback chain na ordem: NVIDIA → OpenRouter → Nous → Cloudflare (até `TOP_N`)
4. Atualizam `config.yaml`:
   - `fallback_providers` (OpenRouter + NVIDIA + Nous + Cloudflare, agent-managed auth quando aplicável)
   - `model.default` (NVIDIA only, text-only)
   - `moa.reference_models` (NVIDIA only, text-only — NOT Nous/Cloudflare)
   - `moa.aggregator` (**multimodal MoE preferencial** quando disponível no NIM: `moonshotai/kimi-k3`, `z-ai/glm-5-3-flash`, `deepseek-ai/deepseek-v4.1-flash`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`; caso contrário text-only high-priority NVIDIA)
5. Reiniciam o gateway para aplicar as mudanças

**⚠️ Regra Crítica:** Modelos Nous e Cloudflare (`provider: custom` + `key_env`) ficam **apenas em `fallback_providers`**, nunca em `moa.reference_models`. O MOA reference_models não resolve `key_env` para providers custom corretamente. Além disso, Cloudflare Workers AI usa endpoint `/ai/run/@cf/...`, que não é OpenAI-compatible; só promover Cloudflare para rota principal/MOA se houver adaptador/provider OpenAI-compatible validado end-to-end.

**Cloudflare Workers AI:** O token atual (`CLOUDFLARE_API_TOKEN`) não tem escopoAccount ID necessário para o endpoint OpenAI-compatível (`api.cloudflare.com/client/v4/accounts/{id}/ai/v1`). O usuário quer Cloudflare no fallback chain quando configurado, mas requer: (1) `CLOUDFLARE_ACCOUNT_ID` no `.env`, (2) token com escopo `AI Gateway:Read`, (3) gateway criado via `gateway.ai.cloudflare.com`.

## Pré-requisitos

- Hermes Agent instalado com pelo menos 1 perfil
- `NVIDIA_API_KEY` no `.env` do perfil (obter em https://integrate.nvidia.com)
- `NOUS_API_KEY` no `.env` do perfil (obter em https://inference-api.nousresearch.com)
- `CLOUDFLARE_API_TOKEN` no `.env` do perfil (obter em https://dash.cloudflare.com/profile/api-tokens)
- `OPENROUTER_API_KEY` no `.env` do perfil (obter em https://openrouter.ai/keys)

## Instalação

```bash
# Clonar o repositório
git clone https://github.com/falagama-cpu/Hermes.git /tmp/hermes-repo

# Instalar no perfil desejado
bash /tmp/hermes-repo/skills/free-llm/scripts/install_free_model_selection.sh <profile>
```

**Nota:** O instalador detecta automaticamente o perfil ativo se nenhum argumento for passado.

O script de instalação:
- Copia os scripts para `~/.hermes/profiles/<profile>/scripts/`
- Cria 3 cron jobs no perfil
- Verifica se as API keys existem no `.env`

## Cron Jobs

| Job | Horário | Script | O que atualiza |
|-----|---------|--------|----------------|
| `update-free-models-14h` | 08:00, 20:00 | `update_free_models.py` | `fallback_providers` |
| `choose-best-free-llm` | 02:00, 14:00 | `choose_best_free_llm.py` | wrapper para o anterior |
| `update-hermes-models` | 09:00, 21:00 | `update_models.py` | `model.default`, `reference_models`, `aggregator` |

## Scripts

### `update_free_models.py`
- Sonda OpenRouter (catálogo `:free`/pricing 0), NVIDIA (candidatos conhecidos de previews free), Nous (catálogo com filtro :free) e Cloudflare Workers AI
- Monta fallback chain na ordem atual: NVIDIA primeiro, depois OpenRouter, depois Nous, depois Cloudflare até `TOP_N`
- Atualiza `fallback_providers` no config.yaml
- Reinicia o gateway ao final

### `update_models.py`
- Sonda NVIDIA previews (lista `NVIDIA_CANDIDATES` hardcoded) via probe HTTP 200 em `integrate.api.nvidia.com/v1`
- Sonda Nous Portal catálogo (`model-catalog.json`) + valida candidatos free conhecidos (`NOUS_FREE_CANDIDATES`) via probe 200
- **NVIDIA by design**: `model.default`, `moa.reference_models` (preset + root) são **exclusivamente NVIDIA text-only**
- **Aggregator multimodal**: o `moa.aggregator` pode ser um modelo multimodal MoE (ex.: `moonshotai/kimi-k3`, `z-ai/glm-5-3-flash`, `deepseek-ai/deepseek-v4.1-flash`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`) quando disponível no NIM; caso contrário cai em text-only high-priority NVIDIA
- Ranking: probe 200 → separa text-only vs multimodal → model.default/ref_models = text-only high-priority → aggregator = multimodal preferencial → fallback text-only high-priority
- Reconciliação: remove entradas `provider: nvidia` órfãs (que saíram da lista NIM atual) do `moa.reference_models` (preset e root) antes de reescrever
- Atualiza cache `provider_models_cache.json` (NVIDIA + Nous)
- Patch YAML via regex (não via PyYAML) — mantém formatação/anchors do config; regex atualizados para indentation real do config (8 spaces para preset, anchor `&id001`)
- Reinicia gateway (`hermes gateway restart`) ao final

**Nota (set/2026, alinhado com falagama-cpu/Hermes):** O script **não** fixa o aggregator em `anthropic/claude-sonnet-5`. O aggregator é selecionado automaticamente:
- **Multimodal MoE preferencial** (se disponível no NIM): `moonshotai/kimi-k3`, `z-ai/glm-5-3-flash`, `deepseek-ai/deepseek-v4.1-flash`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`
- **Fallback**: text-only high-priority NVIDIA (próximo após default+refs, ex.: `deepseek-ai/deepseek-v4-flash-0731`)

Nous/Cloudflare aparecem **apenas** em `fallback_providers` via `update_free_models.py`.

### `choose_best_free_llm.py`
- Thin wrapper que chama `update_free_models.py` (mantido para compatibilidade com cron antigo)

## Config Keys Atualizadas

| Key | Formato | Escrito por |
|-----|---------|-------------|
| `fallback_providers` | lista de `{provider, model, base_url, key_env?}` | `update_free_models.py` |
| `model.default` | string (model ID) | `update_models.py` |
| `moa.presets.default.reference_models` | lista de `{provider, model, enabled}` | `update_models.py` |
| `moa.reference_models` (root) | mesmo formato acima | `update_models.py` |
| moa.aggregator | {provider, model} | `update_models.py` — **multimodal MoE preferencial** (ex.: `moonshotai/kimi-k3`, `z-ai/glm-5-3-flash`, `deepseek-ai/deepseek-v4.1-flash`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `meta/muse-glimmer-30b`) quando disponível no NIM; caso contrário text-only high-priority NVIDIA (próximo após default+refs) |

**Nota:** `moa.aggregator` é NVIDIA (ex.: `deepseek-ai/deepseek-v4-flash-0731`), **não** fixo em `anthropic/claude-sonnet-5`. O script `update_models.py` seleciona automaticamente. Nous/Cloudflare ficam apenas em `fallback_providers`.

## API Keys Necessárias

```
# Em ~/.hermes/profiles/<profile>/.env
NVIDIA_API_KEY=nvapi-...
NOUS_API_KEY=sk-nous-...
OPENROUTER_API_KEY=sk-or-v1-...
ANTHROPIC_API_KEY=sk-ant-...
```

Obter em:
- NVIDIA: https://integrate.nvidia.com/rocfm/api/key (conta NVIDIA gratuita)
- Nous: https://portal.nousresearch.com (conta Nous Research)
- OpenRouter: https://openrouter.ai/keys (conta OpenRouter gratuita)
- Anthropic: https://console.anthropic.com/ (conta Anthropic)

## Gateway Restart

Os scripts automaticamente executam `hermes gateway restart` ao final de cada atualização bem-sucedida. Isso garante que:
- O novo `model.default` seja carregado
- A nova `fallback_providers` seja usada
- Os novos `reference_models` entrem em efeito
- O `moa.aggregator` selecionado (multimodal ou text-only) entre em efeito

Se o gateway não estiver rodando, o `restart` falha silenciosamente (exit code ≠ 0) mas o script continua.

## Troubleshooting

**Cron falha com "Network is unreachable":**
- O perfil não tem acesso à internet no momento do cron
- Verifique proxy/firewall

**Nenhum modelo NVIDIA encontrado:**
- `NVIDIA_API_KEY` ausente ou inválida
- Verifique: `grep NVIDIA_API_KEY ~/.hermes/profiles/<profile>/.env`

**Gateway não reinicia:**
- O comando `hermes` não está no PATH do ambiente cron
- Use caminho absoluto: `$(which hermes) gateway restart`

**`update-hermes-models` falha com `ERRO: sem modelos free`:**
- Isso indica probe NVIDIA vazio no horário do cron, geralmente falha transitória de rede/provider.
- O script **é fail-soft**: se `get_nvidia_models()` retorna vazio, mantém o config atual e sai 0 (não quebra o cron).

**`ModuleNotFoundError: No module named 'requests'` nos crons:**
- O ambiente do cron não tem o módulo `requests` instalado
- Instale no diretório dos scripts do perfil: `pip3 install requests --target=/home/fabio/.hermes/profiles/<profile>/scripts/`
- O script original usa `urllib` (stdlib), mas as versões atuais usam `requests` para timeout total confiável

**Regex de patch YAML sensível à indentação:**
- O `patch_moa_aggregator()` usa regex ancorado no anchor YAML `&id001` (preset) e na indentação real do config (8 spaces para provider/model sob `presets.default.aggregator`)
- Se a estrutura do config.yaml mudar (ex.: anchor removido, indentação alterada), o regex falha silenciosamente
- Verifique com: `grep -n 'aggregator: &id001' config.yaml` antes de rodar o script

**NVIDIA NIM rate limit** (conta falagama@gmail.com): até **40 rpm**. Os scripts sondam ~19 req/run no `update_free_models` e ~12 no `update_models` — dentro do limite para run isolado. Não agrupe runs no mesmo minuto (crons já espaçados).

**model.default como "default" (string literal):**
- Indica que `update_models.py` não rodou com sucesso
- Rode manualmente: `HERMES_HOME=$HOME/.hermes/profiles/<profile> python3 update_models.py --check`

**moa.aggregator não é um multimodal MoE esperado:**
- Indica que nenhum modelo multimodal MoE estava disponível no NIM no momento do probe
- O script cai no fallback text-only high-priority NVIDIA (ex.: `deepseek-ai/deepseek-v4-flash-0731`)
- Para forçar multimodal: adicione o modelo desejado em `MULTIMODAL_AGG_CANDIDATES` e `NVIDIA_CANDIDATES` no script
- Verifique disponibilidade: `HERMES_HOME=$HOME/.hermes/profiles/<profile> python3 -c "from scripts.update_models import get_nvidia_models; import os; print([m['id'] for m in get_nvidia_models() if 'kimi-k3' in m['id'] or 'glm-5-3' in m['id'] or 'v4.1-flash' in m['id'] or 'nano-omni' in m['id'] or 'muse-glimmer' in m['id']])"`

## Desinstalação

```bash
# Remover scripts
rm -f ~/.hermes/profiles/<profile>/scripts/update_free_models.py
rm -f ~/.hermes/profiles/<profile>/scripts/update_models.py
rm -f ~/.hermes/profiles/<profile>/scripts/choose_best_free_llm.py

# Remover cron jobs
# Edite ~/.hermes/profiles/<profile>/cron/jobs.json e remova os 3 jobs
# Ou simplesmente delete jobs.json e recrie os jobs manualmente
```

## Referências

- [NVIDIA NIM Documentation](https://docs.nvidia.com/nim/)
- [Nous Portal](https://portal.nousresearch.com)
- [OpenRouter Documentation](https://openrouter.ai/docs)
- [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs)
