# Auditoria comparativa — arquiteturas e versões

Fluxo para comparar arquiteturas (MAIN × MoA × Council) ou versões (V5.1 × V5.2) com o mesmo enunciado, sem tocar a produção.

## 1. Isolamento

Nunca rode contra o perfil de produção (cria sessão, grava histórico). Use um `HERMES_HOME` descartável:

```bash
export HERMES_HOME=/abs/dir/audit/profile
mkdir -p "$HERMES_HOME"
cp ~/.hermes/profiles/<perfil>/config.yaml "$HERMES_HOME/config.yaml"
cp ~/.hermes/profiles/<perfil>/.env "$HERMES_HOME/.env"   # chmod 600
hermes doctor   # OBRIGATÓRIO no 1º uso: monta o venv em $HERMES_HOME/installs/...
```

Saídas em `/abs/dir/audit/respostas/`. O `council.py` aceita `COUNCIL_STATE_DIR` para isolar o estado.

## 2. Execução

Mesmo enunciado para as três. Use `--query-file` (o `-q` interpreta backticks/`$()` do shell).

| Abordagem | Comando | Chamadas |
|---|---:|---:|
| MAIN | `hermes chat --query-file teste.txt --oneshot -Q --yolo` | 1 |
| MoA | `hermes chat --query-file teste_moa.txt --oneshot -Q --yolo` (arquivo = `/moa ` + enunciado) | 4 |
| Council | `uv run python run_council.py teste.txt 4` (wrapper) | 9 |

O MoA é invocado pelo prefixo `/moa ` no início do prompt — o aggregator gera **orientação privada** para o MAIN, que produz a resposta final.

**Wrapper do Council:** o `council.py` recebe a query como arg posicional (`nargs="+"`); passar enunciado com código via shell corrompe backticks. Use um wrapper Python que lê o arquivo e chama `council.run_council(query, top_n=4)`.

## 3. Métricas

- **MAIN/MoA:** `<HERMES_HOME>/state.db`, tabela `sessions` — `started_at`, `ended_at`, `input_tokens`, `output_tokens`, `estimated_cost_usd`, `message_count`, `tool_call_count`. Duração = `ended_at - started_at`.
- **Council:** o JSON de saída traz `total_elapsed_s`, `chairman_model`, `stage1` (membro/modelo/tempo/erro/`usage`), `stage2` (ranking), `swaps` e `tokens` (`{input, output, total}`) — só quando o provedor informa `usage`; nunca estimado.
- **Tool calls:** `tool_call_count` > 0 indica que o agente usou tools (ex.: `web_search`) — confundidor em testes de pesquisa.

## 4. Avaliação cega

1. Extraia as respostas finais e anonimize com códigos aleatórios (`R-<hex>`), guardando o mapa à parte.
2. Envie só códigos + textos + critérios a um subagente (`delegate_task`) que não gerou as respostas.
3. Critérios 0–10: precisão factual, profundidade, identificação de erros, fundamentação, consistência lógica, utilidade prática (total 60).
4. Exija calibragem (mediana 4–6) e um gabarito quando houver (ex.: auditoria de código).

## 5. Pitfalls

- **MAIN dedupeado:** se o `moa.aggregator` é o mesmo modelo do `model.default`, o MAIN não participa do Council. Confirme com `load_council_members`.
- **Swaps mudam a composição:** a cadeia de reservas pode falhar (429/401) e reduzir o número efetivo de membros. Leia `swaps` antes de comparar.
- **Assimetria de tools:** MAIN/MoA têm `web_search` no loop; o Council não. Em testes de pesquisa, registre o confundidor.
- **Tokens do Council:** disponíveis no JSON quando o provedor informa `usage`; se o provedor não informar, reporte NÃO DISPONÍVEL (não estime).
- **Executar em sequência** (MAIN → MoA → Council) para não disputar cota dos provedores gratuitos.

## 6. Verificação de integridade da produção

Antes de concluir, prove que a produção não foi tocada:

- `config.yaml` — o mtime deve ser anterior ao início da auditoria.
- `cron/jobs.json` — o scheduler reescreve o arquivo a cada tick (watchdog a cada 15 min), então **mtime não é evidência de alteração**. Verifique o campo `updated_at` e, por job, `enabled`/`state`/`last_run_at`.
- Sessões — `<HERMES_HOME>/state.db` deve conter apenas as sessões da auditoria (ex.: 15 para 5 testes × 3 abordagens); a produção não deve ter sessões novas.
- Gateway — confirme que não foi reiniciado (`systemctl --user is-active hermes-gateway`).

## 7. Estrutura de entrega

```
inventario.md          # fase 1: modelos, provedores, chamadas, scripts
protocolo.md           # fase 2: enunciados e critérios
testes/                # um arquivo por teste (+ variante _moa com prefixo /moa)
respostas/              # main_*.txt, moa_*.txt, council_*.json, anonymized.json, _mapa_codigos.json
metricas.csv           # teste, abordagem, modelos, provedores, chamadas, duração, tokens, erros
avaliacao_cega.json    # notas, justificativas, ranking
relatorio_final.md     # relatório comparativo
```

## 8. Validação paralela entre versões (V5.1 × V5.2)

A mesma lógica, comparando duas versões do mesmo sistema. O que muda:

### 8.1 Congelamento
Grave hashes das duas versões e das configs compartilhadas num manifesto (`freeze_manifest.json`) e escreva um script que recalcula e compara no fim. "Todos os hashes conferem" é a prova de integridade; sem isso o experimento não garante que as versões não mudaram no meio.

### 8.2 Orquestrador retomável
Um script que percorre entradas × versões, grava um JSON por execução e **pula o que já existe**. Se o processo morrer, reiniciar não refaz trabalho. Cada execução é um subprocesso com timeout; a falha de uma é registrada e as demais continuam.

### 8.3 Instrumentação observacional de HTTP
Para medir tentativas HTTP sem alterar o algoritmo sob teste, faça monkey-patch de `httpx.AsyncClient.send` **antes** de importar o módulo testado:

```python
_orig = httpx.AsyncClient.send
async def counting(self, request, **kwargs):
    stats["http_attempts"] += 1
    u = str(request.url)
    stats["http_by_url"][u] = stats["http_by_url"].get(u, 0) + 1
    resp = await _orig(self, request, **kwargs)
    c = str(resp.status_code)
    stats["http_by_status"][c] = stats["http_by_status"].get(c, 0) + 1
    return resp
httpx.AsyncClient.send = counting
```

Isso captura retries internos que o campo `calls` do resultado não captura.

### 8.4 Juízes independentes (avaliação cega)
- Escolha juízes que **não estejam nas equipes testadas** — juiz da mesma equipe é circular.
- Randomize A/B por pergunta com semente fixa; use **sementes diferentes por juiz** para diluir viés de posição.
- Envie só "Pergunta", "Resposta A" e "Resposta B"; o juiz nunca vê a versão.
- Mensagem de sistema exigindo JSON-only e `max_tokens` alto: com respostas longas o modelo raciocina em prosa antes do JSON.
- Parsing robusto: tente `json.loads`; se falhar, `raw_decode` a partir de cada `{` e fique com o maior objeto que tenha as chaves "A"/"B".
- Reexecute em 504 Gateway Timeout; uma falha não aborta o lote.

### 8.5 Comparabilidade entre versões
- **`calls` não é comparável**: V5.1 conta 2 por swap, V5.2 conta 1. Use `tentativas HTTP`.
- **`elapsed_s` por membro exclui retries de swap** no V5.2 (só a última tentativa). Use a latência total (`wall_s`).
- **Rate limits confundem**: 429 concentrados em modelos específicos podem refletir a janela do experimento, não a arquitetura. Declare como limitação.
- **Não declare vencedor** com amostra pequena ou diferença pequena; reporte por categoria e geral, com limitações.

### 8.6 Pitfalls de execução
- **Log de processo longo redirecionado para arquivo fica vazio por buffering.** Python bufferiza stdout quando não é TTY. Use `python -u`, `flush=True`, ou monitore os arquivos de resultado em vez do log.
- **Juiz que devolve prosa em vez de JSON** com entrada longa: não é erro do modelo, é falta de instrução. Reforce a mensagem de sistema e aumente `max_tokens`.

## 9. Auditoria somente leitura de mudanças vs baseline congelado

Quando o pedido é "o que mudou desde o congelamento", o manifesto guarda **hashes, não conteúdo**. Sem uma cópia do conteúdo baseline, o diff é reconstruído — e só é evidência se o hash fechar.

### 9.1 Reconstruir o baseline a partir do hash
- Encontre uma referência de conteúdo (backup, cópia isolada, commit anterior) e calcule o diff dela para o estado atual.
- Se um registro de sessão mostra o diff que essa referência tinha para o baseline, os hunks que diferem são os candidatos à mudança.
- **Inverta os hunks candidatos no estado atual e confirme que o SHA-256 resultante bate com o hash congelado.** Um diff não verificado é hipótese, não evidência.
- Se nenhum hunk reproduz o hash, a mudança não é uma edição local reversível: procure outra referência antes de concluir.

### 9.2 Atribuição de commit
- O diretório do arquivo pode **não ser repositório git** mesmo existindo um repo do projeto que contém outro arquivo de mesmo nome. Rode `git rev-parse --is-inside-work-tree` no próprio diretório e `git log -- <path>` a partir desse worktree antes de declarar "sem commit".
- O repo upstream pode ter um arquivo de mesmo nome com conteúdo diferente — compare hashes, não nomes.
- Antes de concluir "edição externa", procure o conteúdo antigo no histórico de sessões (`session_search`): a sessão que gravou o diff baseline→congelado costuma estar lá.

### 9.3 Documentação vs comportamento real
- Para afirmar "o campo X está no resultado", **instancie o objeto de resultado e serialize** (`to_json`/`asdict`) e liste as chaves. Farear o código mostra variáveis internas que nunca são exportadas: sets e locais de uma função não aparecem no contrato de saída.
- Confirme os nomes de campo contra o `dataclass` de resultado, não contra o corpo das funções.

### 9.4 Geração de manifesto (freeze e archive)
- **`find | xargs sha256sum > manifesto` captura o próprio manifesto vazio** — o shell cria o arquivo antes do `find` rodar, e a verificação seguinte falha com o hash de arquivo vazio. Exclua o manifesto por nome: `find . -type f ! -name 'MANIFEST.sha256'`.
- **`sha256sum -c` localiza a saída**: em locale pt-BR o sucesso é `SUCESSO` e a falha `FALHOU`, não `OK`/`FAILED`. Um `grep` por `OK` retorna 0 e parece falha.
- Em archive, o manifesto não pode incluir a si mesmo (auto-hash é impossível); documente isso no `ARCHIVED.md`.

### 9.5 Archive de projeto
- `ARCHIVED.md`: status ARQUIVADO, motivo, resultado dos testes e limitações, problemas conhecidos, localização dos relatórios/resultados, data.
- `ARCHIVE_MANIFEST.sha256`: SHA-256 de todos os arquivos, excluindo o próprio manifesto.
- Preserve tudo (não apague nem sobrescreva), não crie cron/serviço/automação, não mova arquivos de produção, e registre que o projeto não deve rodar nem receber desenvolvimento sem autorização explícita.
