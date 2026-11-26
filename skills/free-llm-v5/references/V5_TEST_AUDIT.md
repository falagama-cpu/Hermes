# V5 — Auditoria de testes (2026-11-26)

Escopo: unificação da V5 num arquivo único (`hermes-free-model-selector-v5.py`, sem
dependência da V4), inventário dos testes existentes, novos testes e defeitos
encontrados na seleção V5.1. Nenhum teste tocou perfil real (HERMES_HOME descartável).

## 1. Prova de equivalência da unificação

| Verificação | Resultado |
|---|---|
| Paridade função a função vs. antiga "V5 integrated" (monkey-patch da V4) — pool real (55 modelos, 6 fontes) | **idêntico** (scores de 6 papéis, MAIN, MOA, fallbacks, reference models, ordem de candidatos) |
| Paridade em 300 pools sintéticos aleatórios (caps/fonte/AA perturbados, ordem embaralhada) | **0 divergências** |
| Sanidade do teste de paridade (mutação proposital de um bônus) | mutação **detectada** |
| `--dry-run` real ponta a ponta (rede + probes), integrated × unificada | mesmo MAIN, MOA, reference models e mesma sequência de probes. Fallbacks diferiram só porque o probe de um modelo deu timeout numa execução e 200 na outra (variação de rede, não de código). |
| Suíte de infraestrutura `tests/free-llm/run_tests.py` (probe 429/401, cota, confiabilidade, quarentena, escrita atômica + restore) | **14/14 na V4 e 14/14 na V5** (`SELECTOR=<arquivo>`) |
| `ruff --select F,E9` | limpo |

## 2. Inventário dos testes que existiam

| Teste | Situação | O que realmente cobria | Decisão |
|---|---|---|---|
| `test_v5_simple.py` | passava | constantes **copiadas dentro do próprio teste** — não importava o seletor | removido (substituído) |
| `test_v5_improvements.py` | **quebrado** (`ModuleNotFoundError: hermes_free_model_selector_v5`) | V5 antiga (não a V5.1) | não publicado |
| `benchmark/test_v5_1_parity.py` | **quebrado** ("Required modules not available": importa nomes de módulo inexistentes) | — | não publicado |
| `benchmark/test_v5_1_simple.py` | passava | só `hasattr` (funções existem) | não publicado |
| `benchmark/test_benchmark.py` | passava | benchmark sem nenhuma asserção; **regrava arquivos de resultado versionados** ao rodar | não é teste |
| council `tests/test_v51.py` | 25/25 OK | encanamento do council (seleção de membros, reservas, TTL not_free…) | mantido (skill council) |
| `tests/free-llm/run_tests.py` | **falhava** fora de ambiente limpo | infra do seletor | corrigido: hermético (ignora `*_BASE_URL`/chaves do shell) e parametrizável (`SELECTOR=`) |

Conclusão: antes desta auditoria **nenhum teste exercitava o código de seleção da V5.1**
contra dados reais; a "paridade V5.1" anterior nunca executou.

## 3. Testes novos — `tests/test_v5_unified.py` (22, offline, < 1 s)

- **Independência:** importa numa pasta sem nenhum arquivo da V4; código sem referência à V4.
- **Rebinds:** `score_role/choose_*` são as versões V5.1; `NOT_FREE_TTL_S == 0`; constantes V5.1 isoladas das da infraestrutura.
- **Golden:** seleção e scores do fixture real (`fixtures/pool_20261126.json`) iguais aos da antiga integrated.
- **Invariantes:** família nunca repete entre papéis; 3 fallbacks com perfis coding/reasoning/long-context; pool vazio/unitário; entrada não é mutada.
- **ChatGPT/Codex:** token válido; singleton vencido → pool; `auth.json` com BOM (Windows); fallback Codex CLI; motivo registrado (ausente / não-JWT / expirando); renovação delegada a `hermes auth refresh openai-codex` com o `HERMES_HOME` certo.
- **Wrapper de cron:** aponta para o arquivo único; sucesso imprime MAIN/FB; `state.json` não atualizado → exit 3.
- **Defeito conhecido A1** como `expectedFailure` (vira alerta quando for corrigido).

## 4. Defeitos encontrados na seleção V5.1 (herdados; NÃO corrigidos — mudam o comportamento)

Medidos no fixture real (55 modelos, 6 fontes), 100 embaralhamentos da mesma lista:

| # | Defeito | Evidência | V4 |
|---|---|---|---|
| A1 | **Resultado depende da ordem do catálogo.** Empates exatos de score (ex.: 79,69 × 79,69) decididos por quem vem primeiro; a rota dentro da família também. | MAIN estável neste pool, mas **63 seleções completas distintas em 100** (MOA/fallbacks/refs mudam). Num pool sem Nous: 2 MAINs (54/46) e 83/100 | 2/100 |
| A2 | **Benchmark Artificial Analysis ignorado** no score (a mistura `AA_WEIGHT=0.85` da V4 sumiu; só heurística por palavras-chave). | modelo nº 1 por AA (score 91,9 na V4) fora do top-6 da V5; um modelo de 1,7B empata com modelos de 550B | usa AA |
| A3 | **Confiabilidade ignorada no score** (latência EMA / falhas recentes); `_REL` só serve para quarentena. | `reliability_penalty` não é chamada pela V5.1 | penaliza |
| A4 | **Bônus de contexto é código morto:** compara a *nota* `_caps.long_context` (0–100) com `MIN_CONTEXT` (65 536 *tokens*). | nunca verdadeiro | usa `_context` (tokens) |
| A5 | **"Bônus de diversidade" constante** (+3 para todos) — não altera ranking. | idem `main_selection_key` | — |
| A6 | **Rota dentro da família ignora qualidade:** `_role_score` ainda não existe quando a rota é escolhida (vale 0). | escolhe por fonte inédita / primeira da lista | usa score + fonte + contexto |
| A7 | **Perdeu a penalidade −25 por provedor repetido nos fallbacks** (correção da V4 após cadeia inteira cair por cota de um único provedor). | ausente em `_v51_fallback_score` | presente |
| A8 | Fallbacks usam **uma única capacidade crua** em vez das fórmulas ponderadas por perfil; pesos `fallback1..3` nunca são usados. | `role in caps → caps[role]` | fórmulas por papel |
| A9 | **Mesmo modelo em dois papéis** por provedores diferentes (ex.: nemotron-3-ultra via OpenRouter e via NVIDIA). | **21/100** ordens (54/100 no pool sem Nous) | 0/100 |

Outros achados operacionais:
- A10 — instalações que copiaram só os arquivos de seleção ficaram **sem `nvidia_official_free.py`**: a validação do badge oficial "Free Endpoint" da NVIDIA é pulada em todo ciclo (log: "módulo nvidia_official_free.py ausente"). A skill unificada inclui o módulo.
- A11 — API da Artificial Analysis respondendo **HTTP 410**; o seletor usa cache antigo (14/53 modelos com benchmark).
- A12 — `hermes auth status` mostra "logged in" para o ChatGPT mesmo com access token vencido; a V4 ignorava a fonte silenciosamente ("sem token válido"). Corrigido na V5 (motivo no log + renovação via Hermes).

## 5. Recomendação

A unificação é segura (equivalente comprovada). A **seleção V5.1 é inferior à V4 em
determinismo e qualidade** (A1, A2, A9). Correção mínima sugerida, medível com o mesmo
fixture antes de adotar: restaurar `score_role` da V4 (AA + confiabilidade) como base
do papel, desempate determinístico por `(score, aa, contexto, model_id)`, contexto em
tokens (`_context`), rota da família pelo score do papel, penalidade −25 de provedor
repetido e deduplicação por modelo-base entre papéis.
