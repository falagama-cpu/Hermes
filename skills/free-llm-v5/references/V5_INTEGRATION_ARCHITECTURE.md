# V5_INTEGRATION_ARCHITECTURE.md

## 1. Objetivo
Projetar a V5 operacional reutilizando a infraestrutura comprovada da V4 e incorporando apenas as melhorias algorítmicas da V5.1, sem modificar a V4 nem ativar a V5.

## 2. Escopo
- Diretório de trabalho: `/home/fabio/hermes_free_llm_v5_isolated`
- V4 de referência: `/home/fabio/hermes_free_llm_v5_isolated/scripts/hermes-free-model-selector-v4.py`
- V5.1 de referência: `/home/fabio/hermes_free_llm_v5_isolated/scripts/hermes-free-model-selector-v5.1.py`
- Nenhuma implementação, chamada externa, alteração de configuração ou cron nesta fase.

## 3. Arquitetura proposta

### 3.1 Camadas
1. **Coleta real** — fetchers da V4.
2. **Normalização** — `enrich()` da V4.
3. **Scoring** — V4 `_score_role_raw` + V5.1 `_score_role_v5_enhanced`.
4. **Seleção** — V4 `choose_main`, `choose_moa`, `choose_fallbacks`; V5.1 `choose_main_v5` após validação de integração.
5. **Validação** — V4 `warmup_model`, `probe_ok`, tratamento de HTTP 429/quota.
6. **Persistência** — V4 `apply_config`, `_atomic_write_text`, backup/history/reliability.
7. **Operação** — V4 `run`, cron, state, gateway restart.

### 3.2 Princípio
A V5 não deve recriar infraestrutura. Deve consumir os objetos produzidos pelos fetchers da V4 e aplicar apenas as melhorias de scoring/seleção, com testes antes de qualquer uso em produção.

## 4. Referências principais
- V4 coleta: `fetch_openrouter` ~885, `fetch_nous` ~932, `fetch_cloudflare` ~1003, `fetch_nvidia` ~1366, `fetch_google` ~1493, `fetch_openai_codex` ~1606, `collect_models` ~1644.
- V4 normalização: `enrich` ~605, `capabilities` ~656.
- V4 persistência: `load_config` ~2207, `apply_config` ~2419, `_atomic_write_text` ~2580, `backup_config` ~2655.
- V4 validação: `warmup_model` ~3495, `run_warmups` ~3629, `probe_ok` ~3651.
- V5.1 scoring/seleção: `_score_role_v5_enhanced` ~296, `choose_main_v5` ~327, `choose_moa` ~472, `choose_fallbacks` ~487.

## 5. Limitações atuais
A V5.1 não possui coleta real, autenticação, HTTP, persistência, warm-up ou integração operacional. Portanto, não pode substituir a V4 até a integração ser implementada e testada.
