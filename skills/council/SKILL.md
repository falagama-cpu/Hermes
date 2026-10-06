---
name: council
version: 1.2.0
author: fabio
license: MIT
description: Run local LLM council (MoA) for cross-model answers.
metadata:
  hermes:
    tags: [council, moa, multi-llm]
---

# Council

Consulte um "council" de LLMs quando o usuário pedir opinião cruzada, comparação entre modelos, ou quiser robustez na resposta.

## When to Use
- "Pergunta ao council" / "o que o council acha"
- Perguntas com múltiplas interpretações ou alto valor em precisão
- Verificação cruzada antes de decidir algo importante

## When NOT to Use
- Perguntas triviais ou factuais simples (council é caro: ~60-90s por consulta)
- Situações que precisam de resposta em tempo real

## Como funciona
O script `/home/fabio/hermes-council/council.py` lê `~/.hermes/profiles/pesquisa/config.yaml` a cada execução (o seletor v4 roda 02/08/14/20h e é o único escritor de model.default, moa.* e fallback_providers). Membros, em ordem de prioridade:

1. **Chairman** = `model.default` (MAIN)
2. `moa.aggregator` (preset default)
3. `moa.reference_models` (enabled)
4. `fallback_providers` — só completam se faltar membro

Dedupe por modelo (ignora `:free`), pula slots sem chave/base_url e `provider: moa`. **1 membro por provider** (custom diferenciado pelo host: Cloudflare ≠ Nous): membros rodam em paralelo e vários no mesmo provider dão 429 (rodada real com 3×NVIDIA + ling: 289s, 1 erro 429, ranking só de 1 membro). Repete provider só se faltar membro.

**Troca automática**: membro que falha no estágio 1 (429, timeout de `COUNCIL_MEMBER_TIMEOUT`=120s ou resposta
vazia, depois dos retries) é trocado **na hora** por um reserva, até `COUNCIL_MEMBER_SWAPS`=2
trocas por posição. Reservas, em ordem:

1. slots do `config.yaml` ainda não usados (MoA/fallbacks);
2. `model-selector/catalog.json` do [free-llm](../free-llm/SKILL.md), por índice da Artificial
   Analysis — sem modelos em quarentena (`reliability.json`, ≥3 falhas) nem recusados por
   plano (`not_free.json`); falha recente vai para o fim da fila.

Nunca usa um provider que já falhou na mesma rodada; entre os demais, prefere o de menor carga
no council. O estágio 2 só pede ranking a quem respondeu, e o parser aceita JSON ou prosa com
`Response X`. Chairman: o MAIN se respondeu; senão o próximo membro que respondeu. As trocas
ficam em `state/last.json` (`swaps`) e em `last.md`.

Como o seletor free-llm reavalia o pool a cada 6h, as reservas acompanham sozinhas o que está
vivo e gratuito — o council não mantém lista própria. Ver membros atuais sem rodar o council:

```bash
cd /home/fabio/hermes-council && COUNCIL_PROFILE=pesquisa uv run python -c "import council;[print(m.name,m.provider) for m in council.load_council_members(4)[0]]"
```

3 estágios:
1. Cada membro responde em paralelo
2. Cada membro ranqueia as respostas anonimizadas
3. Chairman sintetiza a resposta final

## Uso

```bash
cd /home/fabio/hermes-council && COUNCIL_PROFILE=pesquisa uv run python council.py "sua pergunta aqui" --members 4
```

**Obrigatório `COUNCIL_PROFILE=pesquisa`** (ou `HERMES_HOME=~/.hermes/profiles/pesquisa`): sem isso o council lê o perfil default `~/.hermes` (versão portátil publicada em falagama-cpu/Hermes).

- `--members N` — quantos modelos no council (default 4; mínimo 2)
- `--json` — imprime JSON em stdout
- Estado: `state/last.json` e `state/last.md` (sobrescritos a cada run)
- `COUNCIL_MEMBER_TIMEOUT` — segundos por membro nos estágios 1-2 (padrão 120)
- `COUNCIL_MEMBER_SWAPS` — trocas automáticas por posição (padrão 2)

## Quando usar
- Perguntas com múltiplas interpretações ou alto valor em precisão
- "Pergunta ao council" / "o que o council acha"
- Verificação cruzada antes de decidir algo importante

## Quando NÃO usar
- Perguntas triviais ou factuais simples (council é caro: ~60-90s por consulta)
- Tarefas que não exigem opinião de múltiplos modelos
- Situações que precisam de resposta em tempo real

## Notas

- Chairman em cadeia: se o MAIN falhar na síntese, o próximo membro que respondeu no estágio 1 assume (em ordem de prioridade). Se todos falharem, erro.
- `provider: custom` sem `key_env` resolve a chave pela base_url (cloudflare.com → CLOUDFLARE_API_TOKEN, nousresearch.com → NOUS_API_KEY).

- Rate limits da NVIDIA são frequentes — retries com backoff já embutidos..
- Sem GPU local: modelos rodam via API (OpenRouter, NVIDIA, custom endpoints)..
- Chaves carregadas de `~/.hermes/profiles/pesquisa/.env` automaticamente..
- Chairman em cadeia: se o MAIN falhar na síntese, o próximo membro que respondeu no estágio 1
  assume (em ordem de prioridade). Se todos falharem, erro.
- `provider: custom` sem `key_env` resolve a chave pela base_url (cloudflare.com →
  CLOUDFLARE_API_TOKEN, nousresearch.com → NOUS_API_KEY).
