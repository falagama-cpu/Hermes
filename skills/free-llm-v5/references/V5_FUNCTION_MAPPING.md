# V5_FUNCTION_MAPPING.md

## Tabela de mapeamento

| Função V4 | Função V5.1 | Destino proposto | Compatibilidade | Risco |
|---|---|---|---|---|
| `enrich` (~605) | nenhuma equivalente | Reutilizar diretamente | Alta | Baixo |
| `capabilities` (~656) | `_score_role_v5_enhanced` (~296) | Adaptar scoring sobre `_caps`/`capabilities` | Média | Médio |
| `fetch_openrouter` (~885) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `fetch_nous` (~932) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `fetch_cloudflare` (~1003) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `fetch_nvidia` (~1366) | nenhuma | Reutilizar diretamente | Alta | Médio |
| `fetch_google` (~1493) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `fetch_openai_codex` (~1606) | nenhuma | Reutilizar diretamente | Alta | Médio |
| `collect_models` (~1644) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `load_config` (~2207) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `apply_config` (~2419) | nenhuma | Reutilizar diretamente | Alta | Médio |
| `_atomic_write_text` (~2580) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `warmup_model` (~3495) | nenhuma | Reutilizar diretamente | Alta | Médio |
| `probe_ok` (~3651) | nenhuma | Reutilizar diretamente | Alta | Baixo |
| `_score_role_raw` (~762) | `_score_role_v5_enhanced` (~296) | Adaptar: V5 usa `capabilities` e `ROLE_WEIGHTS_V5` | Média | Médio |
| `score_role` (~829) | `score_role_v5` (~459) | Adaptar interface e pesos | Média | Médio |
| `choose_main` (~1939) | `choose_main_v5` (~327) | Adaptar após validar campos e comportamento | Média | Alto |
| `choose_moa` (~1982) | `choose_moa` (~472) | Validar integração; nomes coincidem mas implementações diferem | Baixa/Média | Alto |
| `choose_fallbacks` (~2146) | `choose_fallbacks` (~487) | Validar integração; nomes coincidem mas implementações diferem | Baixa/Média | Alto |
| `_unused_source_candidates` (~1966) | `_unused_source_candidates` (~534) | Validar equivalência | Média | Médio |
| `choose_moa_references` (~2024) | `choose_moa_references` (~549) | Validar equivalência | Média | Médio |

## Funções ausentes na V5.1
- Fetchers reais.
- `load_dotenv`, `env_first`.
- `warmup_model`, `probe_ok`.
- `load_config`, `apply_config`, backup/history/reliability.
- `run`, cron, state.

## Funções duplicadas com risco de incompatibilidade
- `choose_moa`
- `choose_fallbacks`
- `_unused_source_candidates`
- `choose_moa_references`
- `score_role`
- `group_by_family`
- `best_route_for_family`

Os nomes coincidem, mas as implementações e campos usados podem divergir. Não copiar automaticamente.
