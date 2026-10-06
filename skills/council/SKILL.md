---
name: council
description: Mixture-of-Agents (MoA) local para seleção de LLM. Stage 1 (respostas paralelas) → Stage 2 (ranking entre pares) → Stage 3 (chairman sintetiza). Lê config.yaml do perfil Hermes dinamicamente; a cada rodada pega a pool de modelos atualizada pelo cron free-LLM selector.
version: 1.0.0
author: falagama-cpu
license: MIT
tags:
  - multi-model
  - moa
  - agente
  - llm
---

# council

Mixture-of-Agents local sobre pool dinâmica de modelos free.

## Uso

```bash
# HERMES_HOME = diretório do perfil Hermes (padrão ~/.hermes)
export HERMES_HOME=~/.hermes/profiles/<perfil>

# Via wrapper (recomendado)
./council.sh "sua pergunta aqui" --members 4

# Ou diretamente com uv
uv run python council.py "sua pergunta aqui" --members 4

# Ver os membros atuais sem rodar o council
uv run python -c "import council;[print(m.name,m.provider) for m in council.load_council_members(4)[0]]"
```

## Membros (config.yaml do perfil, relido a cada execução)

Prioridade:

1. **Chairman** = `model.default` (MAIN)
2. `moa.aggregator` (preset default)
3. `moa.reference_models` (enabled)
4. `fallback_providers` — só completam se faltar membro

Dedupe por modelo (ignora `:free`); pula slots sem chave/base_url e `provider: moa`.
Com o seletor free-LLM v4, os slots MoA são escolhidos por qualidade (papel moa,
intel-pesado) e fabricantes distintos — melhor para opinião cruzada que os fallbacks,
escolhidos por cobertura de falha. `provider: custom` sem `key_env` resolve a chave pela
base_url (cloudflare.com → `CLOUDFLARE_API_TOKEN`, nousresearch.com → `NOUS_API_KEY`).

Council × `/moa` do Hermes: o council tem ranking cruzado anônimo entre os membros antes
da síntese (mais robusto, ~60-90s); o `/moa` vai direto references → aggregator (rápido).

## Estratégias

### Pool dinâmica (`pool.py`)
Lê `reliability.json` + `catalog.json` do seletor v4, health-check n modelos, monta pool só com os que respondem. Evita rate limit de slots fixos no config.yaml.

### Persona `ponyetail` (auto ativo em tarefas de código)
Heuristica `_is_coding_task`:
- +codigo: `refactor`, `corrige`, `def foo(`, ` ```python`, `traceback`
- -arquitetura: `arquitetur`, `design`, `planej`, `analis`

Aplica `personas/ponytail.md` (SKILL.md original do repo DietrichGebert/ponytail) como system prompt em 1 membro — nunca chairman.

Forçar/desativar:
```bash
uv run python council.py "tarefa" --no-ponytail
```

### Chairman
`model.default` do config.yaml = chairman. Se falhar, fallback pro primeiro membro que responder.

### Variáveis de ambiente
- `HERMES_HOME` — perfil Hermes (lê `config.yaml` e `.env` dali)
- `COUNCIL_STATE_DIR` — onde gravar `last.json`/`last.md` (padrão `./state`)
- `CLOUDFLARE_ACCOUNT_ID` — usado por `pool.py` para montar a URL da Cloudflare

## Arquivos
- `council.py` — engine core (3 stages)
- `pool.py` — descoberta de modelos vivos
- `run_example_final.py` / `run_example_pool.py` — runners específicos
- `benchmark_ponytail.py` — benchmark antes/depois
- `personas/ponytail.md` — persona minimalista (YAGNI, stdlib-first)

## Dependências
```
httpx pyyaml
```
