---
name: council
version: 1.4.1
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
- Verificação factual estrita de detalhes (RFCs, APIs, números exatos): o chairman sintetiza as respostas dos membros e pode propagar alucinações — em auditoria comparativa o Council teve a menor precisão factual entre as três arquiteturas

## Como funciona
O script `<skill_dir>/council.py` lê `<perfil>/config.yaml` a cada execução (o seletor free-llm v5 roda 02/10/18h e é o único escritor de model.default, moa.* e fallback_providers; o V4 permanece como infraestrutura). Membros, em ordem de prioridade:

1. **Chairman** = `moa.aggregator` — o modelo que o free-llm escolheu para o MoA
2. `model.default` (MAIN) — sem MoA configurado, MAIN assume o lugar de chairman
3. `moa.reference_models` (enabled)
4. `fallback_providers` — só completam se faltar membro

Dedupe por modelo (ignora `:free`): em 1ª passada, a identidade do modelo sobrepõe a do provedor — se o MAIN ou o chairman tiver o mesmo nome de modelo, o slot é excluído. Em 2ª passada, permite-se o mesmo provedor com modelos distintos. As exclusões por deduplicação são registradas em `deduped` (modelo, provedor e motivo). Membros repetem provedor só se faltar vaga após a 1ª passada.

**Rastro:** `state/last.json` carrega `deduped` (array com modelo, provider e razão).

**Atenção ao MAIN:** o dedupe por modelo também remove o MAIN quando o `moa.aggregator` aponta para o mesmo modelo (ex.: aggregator `nvidia/nemotron-3-ultra-550b-a55b` e MAIN `nvidia/nemotron-3-ultra-550b-a55b:free`). O Council **não ouve o MAIN** nesse caso. Confirme a composição real com `load_council_members` antes de concluir que o MAIN participa.

**Troca automática**: membro que falha no estágio 1 (429, timeout de `COUNCIL_MEMBER_TIMEOUT`=120s ou resposta
vazia, depois dos retries) é trocado **na hora** por um reserva, até `COUNCIL_MEMBER_SWAPS`=2
trocas por posição. Reservas, em ordem:

1. slots do `config.yaml` ainda não usados (MoA/fallbacks);
2. `model-selector/catalog.json` do seletor free-llm **V5** (`../free-llm-v5/`), por índice da Artificial
   Analysis — sem modelos em quarentena (`reliability.json`, ≥3 falhas) nem recusados por
   plano (`not_free.json`, com TTL de `COUNCIL_NOT_FREE_TTL_S`); falha recente vai para o fim da fila.

Nunca usa um provider que já falhou na mesma rodada; entre os demais, prefere o de menor carga
no council. O estágio 2 só pede ranking a quem respondeu, e o parser aceita JSON ou prosa com
`Response X`. Chairman: o MoA se respondeu; senão o próximo membro que respondeu. As trocas
ficam em `state/last.json` (`swaps`) e em `last.md`.

Como o seletor free-llm reavalia o pool a cada 8h, as reservas acompanham sozinhas o que está
vivo e gratuito — o council não mantém lista própria. Ver membros atuais sem rodar o council:

```bash
cd <skill_dir> && COUNCIL_PROFILE=<perfil> uv run python -c "import council;[print(m.name,m.provider) for m in council.load_council_members(4)[0]]"
```

3 estágios:
1. Cada membro responde em paralelo
2. Cada membro ranqueia as respostas anonimizadas
3. Chairman sintetiza a resposta final

## Uso

```bash
cd <skill_dir> && COUNCIL_PROFILE=<perfil> ./council.sh "sua pergunta aqui" --members 4
# Windows: $env:COUNCIL_PROFILE="<perfil>"; .\council.ps1 "sua pergunta aqui" --members 4
```

`<skill_dir>` = pasta desta skill (ex.: `~/.hermes/profiles/<perfil>/skills/council`).

**Obrigatório `COUNCIL_PROFILE=<perfil>`** (ou `HERMES_HOME=~/.hermes/profiles/<perfil>`): sem isso o council lê o perfil default `~/.hermes`.

Requisitos: [`uv`](https://docs.astral.sh/uv/) (instala Python ≥ 3.10, `httpx` e `pyyaml` no 1º uso) e o perfil com o seletor free-llm **V5** instalado (ele preenche MAIN/MoA/fallbacks que viram membros).

- `--members N` — quantos modelos no council (default 4; mínimo 2)
- `--json` — imprime JSON em stdout
- Estado: `state/last.json` e `state/last.md` (sobrescritos a cada run)
- `COUNCIL_MEMBER_TIMEOUT` — segundos por membro nos estágios 1-2 (padrão 120)
- `COUNCIL_MEMBER_SWAPS` — trocas automáticas por posição (padrão 2)

## Auditoria comparativa (MAIN × MoA × Council)

Para comparar arquiteturas ou versões com o mesmo enunciado e sem tocar a produção, veja
`references/comparative-audit.md` — isolamento por `HERMES_HOME`, comandos de execução,
extração de métricas, instrumentação observacional de HTTP, avaliação cega com juízes
independentes, congelamento/integridade, auditoria somente leitura de mudanças vs baseline
congelado e estrutura de entrega.

Para verificar se o Council continua lendo corretamente o que o seletor grava, veja
`references/compatibility-audit.md` — contrato de config/catálogo, comparação de versões,
seleção read-only e classificação.

Para alterar esta skill sem risco, veja `references/skill-maintenance.md` — backup, hashes,
 testes isolados, verificação read-only, integridade da produção e rollback.

## Notas

- Chairman em cadeia: se o modelo do MoA falhar na síntese — ou for trocado no estágio 1 (429/timeout) — o próximo membro que respondeu assume, virando "chairman substituto" em `state/last.json` (`swaps`). Se todos falharem, erro.
- **provider: gemini**/`google`: o council chama `/chat/completions`, que no Gemini fica em `<base>/openai` — base_url `.../v1beta` é reescrita para `.../v1beta/openai` automaticamente; sem isso dá 404.

- **Health check (`pool.py` C3):** as 13 das 67 modelos Gemini são classificadas via `_testa()` com fallback `base_url`/`openai`. Classificações: `autenticacao` (401/403), `nao_encontrado` (404), `rate_limit` (429), `servidor` (≥500), `timeout`, `transporte`, `resposta_invalida`, `resposta_vazia`, `sem_credencial`. HTTP 200 não basta — exige `choices[0].message` válido e não-vazio. O helper `_chat_base_url()` de `council.py` adapta a rota sem duplicação.
- `provider: openai-codex` é pulado com aviso: login ChatGPT (OAuth) não expõe `/chat/completions` por chave. Quando um GPT virar MAIN, o council usa o próximo slot.
- `provider: custom` sem `key_env` resolve a chave pela base_url (cloudflare.com → CLOUDFLARE_API_TOKEN, nousresearch.com → NOUS_API_KEY).
- **Cloudflare exige account além do token:** o endpoint Workers AI é `https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1`. Sem `CLOUDFLARE_ACCOUNT_ID` a URL fica inválida (`.../accounts//ai/v1`) — o candidato não deve ser selecionado nem executado. Implementações que montam a URL de um template precisam checar o account antes de selecionar.
- Rate limits da NVIDIA são frequentes — retries com backoff já embutidos.
- 429 de um membro no estágio 1 é rejeitado na hora (swap ao invés de backoff×3 no mesmo provider): as cotas são por conta, retries no membro que já retornou 429 só estouram rate limit e atrasam o swap — veja o seletor free-llm **V5** (429 falso-verde / cota diária por fonte).
- Sem GPU local: modelos rodam via API (OpenRouter, NVIDIA, custom endpoints).
- Chaves carregadas automaticamente de `<perfil>/.env`.
- **Consulta longa vira background**: com 4 membros o `council.sh` leva ~5-9 min e passa do cap de foreground do terminal, sendo promovido a processo rastreado com notificação. NUNCA re emita o mesmo comando depois disso — use `process(action='poll', session_id=...)` ou espere a notificação de conclusão.
- **Recusa de membro não é veredito**: em temas sensíveis a policy (bypass de licenciamento, DRM, jailbreak), um ou mais membros recusam e o chairman pode sintetizar a recusa, descartando as respostas técnicas dos demais. Rode o council quando o usuário pedir, leia `state/last.md` inteiro (estágio 1 + rankings, não só a resposta final), reporte a divisão — quem respondeu, quem recusou, o que cada um disse — e entregue a resposta técnica justificada pela sua própria evidencia. A recusa vale como sinalizador de limite, não como resposta.
- A composição muda a cada 8h: o seletor free-llm reescreve `moa.aggregator`/fallbacks — leia os membros sempre no momento do run (`load_council_members`), nunca de um cache da sessão.
- **Observabilidade:** `state/last.json` traz, além dos estágios, `deduped` (modelos excluídos e motivo), `reserves_used`, `stage_times`, `calls`, `tokens` (somente quando o provedor informa `usage`; nunca estimado).
- **Health check (`pool.py`):** cobre nvidia, openrouter, nous, cloudflare e google/gemini (via `<base>/openai`, mesma adaptação do `council.py`) e classifica falhas em `autenticacao`, `nao_encontrado`, `rate_limit`, `servidor`, `timeout`, `transporte`, `resposta_invalida`, `resposta_vazia` ou `sem_credencial`.
- **Compatibilidade com o seletor V5:** o Council lê `config.yaml` a cada execução (sem cache) e o V5 grava as mesmas chaves pelo mesmo `apply_config` do V4 — a composição acompanha o seletor sem adaptação.
- **TTL de `not_free.json`:** V5 usa `NOT_FREE_TTL_S = 0` — exclusões são momentâneas, re-probed a cada execução. O Council mantém seu próprio `COUNCIL_NOT_FREE_TTL_S` (padrão **24h**). Entra em quarentena apenas se a **idade** da entrada (`now - ts`) for < `COUNCIL_NOT_FREE_TTL_S`; entradas mais antigas são ignoradas, não bloqueando o modelo. O arquivo compartilhado permanece intocado; não cria-se quarentena independente no Council.