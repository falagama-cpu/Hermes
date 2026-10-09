# Adicionar uma fonte/provedor ao pool free

## 1. Triagem antes de codar (sem gastar crédito)

- Liste modelos com a chave: `GET /v1/models` (OpenAI/xAI), `GET .../v1beta/models` (Gemini, header `x-goog-api-key`). Listagem não cobra; chamada de chat cobra.
- Classifique: **free tier real** (Gemini AI Studio sem billing), **sem free tier** (xAI: resposta `permission-denied` "used all available credits" = conta paga zerada; créditos só via compra/promo/programa de dados) ou **OAuth de assinatura** (`openai-codex` no `auth.json`: sem API key, mas lista e informa cota — ver seção OAuth abaixo).
- Ao inspecionar `.env`/`auth.json`, imprima só NOMES e tamanhos de valor (`awk -F= '{print $1, length($2)}'`) — nunca dumpe strings do JSON: tokens OAuth vazam para o log da sessão.
- "Funciona aqui" do usuário não prova cota da API: o app/site do provedor tem cota separada. Confirme pelo endpoint de cota/listagem antes de prometer a fonte.
- Contexto pequeno não é o obstáculo típico — `HERMES_MIN_CONTEXT` (64K) existe porque prompt de sistema + ferramentas do Hermes ocupam ~20–30K; não baixe para encaixar modelo 8–32K como MAIN/fallback.
- Mostre a tabela fonte × chave × free? × viável ao usuário e pergunte antes de implementar.

## 2. Pontos de código (`hermes-free-model-selector-v4.py`)

1. `SOURCE_ORDER` + `SOURCE_BONUS`.
2. `fetch_<fonte>()` (lista, filtra só texto, monta dict `{id,name,description,context_length}` → `enrich(raw, "<fonte>")` → `base_eligible`) e registro em `collect_models()`.
3. `_free_reason()` — motivo auditável.
4. `PROVIDER_ENDPOINTS` — use o **provider nativo do Hermes** quando existir (`plugins/model-providers/<nome>/`, `hermes_cli/auth.py`); `key_env` explícito para não depender da rotação do pool de credenciais.
5. `_resolve_key()` — ignore valores curtos (placeholder).
6. Probe/warm-up: se o endpoint OpenAI-compat difere da `base_url` gravada (Gemini: `.../v1beta/openai`), troque só a URL do probe.
7. Regra de "free" pelo erro do probe → `mark_not_free()` (Cloudflare 403 `5035`; Gemini 429 `limit: 0`).
8. Fonte com cota diária minúscula: 429 = não selecionar agora, mas **não** registrar falha em `reliability.json` (quarentena é permanente até um sucesso, e a cota volta sozinha); cachear o probe por execução e reaproveitá-lo em probes repetidos e no warm-up (1 req/modelo/execução).
9. Timeout de probe maior para modelos com thinking (30s).
10. Fonte sem chave → `return []` com aviso, mesmo que o `/models` seja público (Nous): listar sem credencial gera seleções que falham no probe com "sem credencial" e desperdiçam o ciclo em máquinas novas.

## 2b. Fonte OAuth (login de assinatura, ex. `openai-codex`)

- Token: leia `$HERMES_HOME/auth.json` (`providers.<p>.tokens.access_token`, depois `credential_pool.<p>`) **só leitura**; descarte se `exp` < 5 min. Nunca renove: o refresh_token é rotativo e gastá-lo derruba o login do Hermes — quem renova é o runtime ao usar.
- Headers: `ChatGPT-Account-ID` sai do claim `https://api.openai.com/auth.chatgpt_account_id` do JWT (sem ele o catálogo vem vazio).
- Catálogo: `GET chatgpt.com/backend-api/codex/models?client_version=99.0.0`, só `visibility: list`.
- Disponibilidade **sem gastar cota**: `GET chatgpt.com/backend-api/wham/usage` → `rate_limit.limit_reached`/`used_percent`/`reset_at`. Cota esgotada → fonte fora da execução, log com a data de reset. Não faça ping de chat (Responses API + cota mensal pequena); o warm-up devolve ok a partir do /wham/usage, e `_resolve_key` precisa devolver o token (senão o warm-up aborta em "sem credencial").
- Entrada no config: provider nativo (`openai-codex`), `base_url` do backend, sem `key_env`.
- Teste o caminho "cota ok" substituindo só `codex_usage_ok` num import do módulo via `importlib` (catálogo, `config_entry`, warm-up reais).
- A AA casa com a variante `(Max)` — superestima plano free; avise o usuário.

## 3. Troca em tempo de execução é do Hermes

Não implemente failover no seletor. O runtime já troca para `fallback_providers`: 429/402 → imediato + cooldown do primário até o reset informado; 401/403 → fallback; 404/410/4xx desconhecido → `format_error`/`model_not_found` → fallback; 5xx/timeout → `api_max_retries` e depois fallback. Confirme em `agent/error_classifier.py` (`_STATUS_HANDLERS`) se o usuário pedir garantia. Requisito do seletor: lista de fallbacks nunca vazia.

## 4. Superfície de instalação/docs + consumidores do config

O `config.yaml` gravado pelo seletor é lido por outras skills (ex.: `council`, que chama `<base_url>/chat/completions` direto). Todo provider novo gravado (`gemini` com base nativa, `openai-codex` OAuth) precisa ser suportado ou pulado nelas no mesmo ciclo — senão o membro dá 404/erro na próxima consulta. Grep `provider ==|_SOURCE_ENDPOINTS|/chat/completions` nos consumidores.

Suporte no `council.py` (2 regras implementadas): `provider: gemini`/`google` → reescreva `<base>/v1beta` para `<base>/v1beta/openai` (rota OpenAI-compat; sem reescrita = 404); `provider: openai-codex` → pule com aviso (OAuth não expõe `/chat/completions` por chave; quando um GPT virar MAIN, o council usa o próximo slot). Ambas via `_chat_base_url()` e `_UNSUPPORTED_PROVIDERS`.

`install.py` (`KEYS` com link de onde obter, `PROVIDERS`, contagem `len(PROVIDERS)`), `required_environment_variables` no frontmatter, `INSTALL.md` (tabela de chaves + troubleshooting do código de erro novo), `README.md`, `SKILL.md` (pipeline + armadilha).

## 5. Teste

Simulação numa cópia do perfil (`config.yaml`, `.env`, `model-selector/` e, para fonte OAuth, `auth.json` chmod 600 — apague depois) com `HERMES_HOME` apontando para ela (não suja `reliability.json` de produção) e grep do log: `grep -iE '<fonte>' last_run.log | grep -E 'probe|reaproveita|NÃO-FREE'` — confirme N elegíveis, 200 selecionado, 429 descartado sem quarentena, segundo uso reaproveitando o probe. Depois `install.py <perfil> --check` mostra `N/N provedores`.
