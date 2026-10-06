---
name: council
description: "Use when the user wants a cross-model answer (LLM council): members answer in parallel, rank each other anonymously, chairman synthesizes. Reads the Hermes profile config.yaml each run."
version: 1.1.0
author: falagama-cpu
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [council, moa, multi-model, llm]
    related_skills: [free-llm]
---

# council

Conselho de LLMs local sobre os modelos já configurados num perfil do Hermes.
Três estágios:

1. Cada membro responde à pergunta em paralelo.
2. Cada membro ranqueia as respostas anonimizadas dos outros.
3. O chairman sintetiza a resposta final usando os rankings como sinal.

## Quando usar / não usar

- Usar: perguntas com várias interpretações, decisões importantes, verificação cruzada.
- Não usar: perguntas triviais ou que precisam de resposta imediata (uma rodada leva ~60-90s).

Council × `/moa` do Hermes: o council tem ranking cruzado anônimo antes da síntese (mais
robusto, mais lento); o `/moa` vai direto references → aggregator (rápido, uso diário).

## Instalação

Requisitos: Python ≥ 3.10 e [`uv`](https://docs.astral.sh/uv/) (ou `pip install httpx pyyaml`).
Passo a passo para Linux, macOS e Windows em [`INSTALL.md`](../../INSTALL.md).

## Perfil

O council lê `config.yaml` e `.env` do perfil, nesta ordem de prioridade:

1. `HERMES_HOME` — caminho do perfil
2. `COUNCIL_PROFILE` — nome do perfil sob o home padrão do Hermes
3. perfil default (`~/.hermes` no Linux/macOS, `%LOCALAPPDATA%\hermes` no Windows)

## Uso

```bash
# Linux/macOS
export COUNCIL_PROFILE=<perfil>
./council.sh "sua pergunta aqui" --members 4
uv run python council.py "sua pergunta" --json        # saída JSON

# Ver os membros atuais sem rodar o council
uv run python -c "import council;[print(m.name,m.provider) for m in council.load_council_members(4)[0]]"
```

```powershell
# Windows (PowerShell)
$env:COUNCIL_PROFILE = "<perfil>"
.\council.ps1 "sua pergunta aqui" --members 4
```

Opções: `--members N` (padrão 4, mínimo 2), `--json`, `--no-ponytail`,
`--out <arquivo.json>`, `--md <arquivo.md>`. Resultado também em `state/last.json` e
`state/last.md` (sobrescritos a cada execução).

## Membros (relidos do config.yaml a cada execução)

Prioridade:

1. **Chairman** = `model.default` (MAIN)
2. `moa.aggregator` (preset default)
3. `moa.reference_models` (enabled)
4. `fallback_providers` — só completam se faltar membro

Dedupe por modelo (ignora `:free`); pula slots sem chave/base_url e `provider: moa`.
**1 membro por provider** (`custom` é diferenciado pelo host: Cloudflare ≠ Nous): as chamadas
são paralelas, e vários membros no mesmo provider estouram o rate limit (429). Só repete
provider se faltar membro.
Com a skill [`free-llm`](../free-llm/SKILL.md) os slots MoA são escolhidos por qualidade e
fabricantes distintos — melhor para opinião cruzada que os fallbacks, escolhidos por
cobertura de falha. `provider: custom` sem `key_env` resolve a chave pela base_url
(cloudflare.com → `CLOUDFLARE_API_TOKEN`, nousresearch.com → `NOUS_API_KEY`).

Chairman indisponível (ex.: 429) → o primeiro membro que respondeu sintetiza.

## Estratégias

### Pool dinâmica (`pool.py`)
Lê `model-selector/reliability.json` do seletor free-llm, faz health-check dos modelos
confiáveis e grava a pool viva em `<tmp>/council_pool_vivos.json`. Útil quando slots do
config estão em rate limit:

```python
import asyncio, council
r = asyncio.run(council.run_council("pergunta", pool_path="<tmp>/council_pool_vivos.json"))
```

### Persona `ponytail` (automática em tarefas de código)
Heurística `_is_coding_task` (`refactor`, `corrige`, `def foo(`, ` ```python`, `traceback`…;
`arquitetura`, `design`, `planejamento` contam contra). Aplica `personas/ponytail.md` como
system prompt em 1 membro — nunca o chairman. Desativar: `--no-ponytail`.

## Variáveis de ambiente

| Variável | Uso |
|---|---|
| `HERMES_HOME` | caminho do perfil Hermes |
| `COUNCIL_PROFILE` | nome do perfil (alternativa a `HERMES_HOME`) |
| `COUNCIL_STATE_DIR` | onde gravar `last.json`/`last.md` (padrão `./state`) |
| `CLOUDFLARE_ACCOUNT_ID` | `pool.py`: monta a URL da Cloudflare (vem do `.env` do perfil) |
| `COUNCIL_POOL_BLOCKLIST` | `pool.py`: modelos a ignorar, separados por vírgula |

Chaves de API são lidas do `.env` do perfil; o council nunca as grava nem imprime.

## Arquivos

- `council.py` — engine (3 estágios)
- `pool.py` — health-check e pool de modelos vivos
- `benchmark_ponytail.py` — compara council com/sem persona ponytail
- `council.sh` / `council.ps1` — wrappers Linux-macOS / Windows
- `personas/ponytail.md` — persona minimalista (YAGNI, stdlib-first; MIT, DietrichGebert/ponytail)
- `pyproject.toml` — dependências (`httpx`, `pyyaml`)
