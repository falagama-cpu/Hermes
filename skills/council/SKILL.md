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
cd <council-dir>
uv run python council.py "sua pergunta aqui" --members 4

# ou via skill:
hermes skill council -- "refatora essa função: def f(x): ..."
```

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
