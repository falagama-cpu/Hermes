---
name: free-llm
description: "Sistema de seleção e troca automática de modelos LLM FREE via cron para qualquer perfil do Hermes Agent. Detecta modelos gratuitos no NVIDIA NIM, Nous Portal e Cloudflare Workers AI. Atualiza config.yaml com fallback chain, e reinicia o gateway para assumir novos modelos."
---

# Free LLM Selection

Sistema automatizado de seleção e troca de modelos LLM gratuitos entre 3 provedores:
- **NVIDIA NIM** — previews free identificados por probe HTTP (integrate.api.nvidia.com)
- **Nous Portal** — modelos com tag `:free` autenticados por Bearer key (inference-api.nousresearch.com)
- **Cloudflare Workers AI** — modelos free via API (api.cloudflare.com) com Bearer token

## Como funciona

1. O cron roda os scripts em `scripts/` que sondam os provedores via HTTP
2. Filtram modelos com preço zero ou tag `:free`
3. Montam uma fallback chain: 2 NVIDIA + slots restantes Nous
4. Atualizam `config.yaml`:
   - `fallback_providers` (NVIDIA + Nous, agent-managed auth)
   - `model.default` (NVIDIA only)
   - `moa.reference_models` (NVIDIA only — NOT Nous)
   - `moa.aggregator` (NVIDIA only, fixed at `anthropic/claude-sonnet-5`)
5. Reiniciarem o gateway para aplicar as mudanças

**⚠️ Regra Crítica:** Modelos Nous (`provider: custom` + `key_env: NOUS_API_KEY`) ficam **apenas em `fallback_providers`**, nunca em `moa.reference_models`. O MOA reference_models não resolve `key_env` para providers custom e falha com 401 quando chamados como references. Apenas `fallback_providers` (gerenciado pelo agente) autentica providers custom corretamente.

## Pré-requisitos

- Hermes Agent instalado com pelo menos 1 perfil
- `NVIDIA_API_KEY` no `.env` do perfil (obter em https://integrate.nvidia.com)
- `NOUS_API_KEY` no `.env` do perfil (obter em https://inference-api.nousresearch.com)
- `CLOUDFLARE_API_TOKEN` no `.env` do perfil (obter em https://dash.cloudflare.com/profile/api-tokens)

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
- Sonda NVIDIA (candidatos conhecidos de previews free) e Nous (catálogo com filtro :free)
- Monta fallback chain: 2 NVIDIA + Nous restantes
- Atualiza `fallback_providers` no config.yaml
- Reinicia o gateway ao final

### `update_models.py`
- Sonda NVIDIA previews para identificar modelos ativos
- Sonda Nous free candidates para cache (não para reference_models)
- Seleciona `model.default` (primeiro NVIDIA de alta prioridade), `reference_models` (top 2 NVIDIA), `aggregator` (próximo não-blacklist)
- Reconhece e remove entradas NVIDIA órfãs em `moa.reference_models`
- Atualiza cache de modelos (`provider_models_cache.json`)
- Reinicia o gateway ao final

### `choose_best_free_llm.py`
- Thin wrapper que chama `update_free_models.py` (mantido para compatibilidade com cron antigo)

## Config Keys Atualizadas

| Key | Formato | Escrito por |
|-----|---------|-------------|
| `fallback_providers` | lista de `{provider, model, base_url, key_env?}` | `update_free_models.py` |
| `model.default` | string (model ID) | `update_models.py` |
| `moa.presets.default.reference_models` | lista de `{provider, model, enabled}` | `update_models.py` |
| `moa.reference_models` (root) | mesmo formato acima | `update_models.py` |
| `moa.aggregator` | `{provider, model}` | `update_models.py` (não-manutenido) |

**Nota:** `moa.aggregator` é fixo em `anthropic/claude-sonnet-5` — os scripts NÃO o alteram.

## API Keys Necessárias

```
# Em ~/.hermes/profiles/<profile>/.env
NVIDIA_API_KEY=nvapi-...
NOUS_API_KEY=sk-nous-...
```

Obter em:
- NVIDIA: https://integrate.nvidia.com/rocfm/api/key (conta NVIDIA gratuita)
- Nous: https://portal.nousresearch.com (conta Nous Research)

## Gateway Restart

Os scripts automaticamente executam `hermes gateway restart` ao final de cada atualização bem-sucedida. Isso garante que:
- O novo `model.default` seja carregado
- A nova `fallback_providers` seja usada
- Os novos `reference_models` e `aggregator` entrem em efeito

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

**model.default como "default" (string literal):**
- Indica que `update_models.py` não rodou com sucesso
- Rode manualmente: `HERMES_HOME=$HOME/.hermes/profiles/<profile> python3 update_models.py --check`

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
- [Hermes Agent Docs](https://hermes-agent.nousresearch.com/docs)
