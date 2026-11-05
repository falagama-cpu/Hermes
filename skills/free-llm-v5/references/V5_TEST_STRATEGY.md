# V5_TEST_STRATEGY.md

## 1. Objetivo
Definir testes necessários para integrar o algoritmo V5.1 à infraestrutura V4, sem executar chamadas reais nesta fase.

## 2. Tipos de teste

### 2.1 Unitários
- `_score_role_v5_enhanced` com campos completos.
- `_score_role_v5_enhanced` com campos ausentes.
- `choose_main_v5` com lista vazia.
- `choose_moa` com famílias/fontes usadas.
- `choose_fallbacks` com menos de 3 candidatos.
- `family_candidates_v5` com grupos duplicados.

### 2.2 Mocks
- Fetchers V4 simulados retornando objetos normalizados.
- Warm-up simulado com HTTP 200, 401, 403, 404, 429, 500.
- Configuração inválida.
- Falha de persistência.
- Quota Google `limit: 0`.
- Cloudflare 4006.

### 2.3 Testes reais controlados (somente com autorização)
- Um modelo por provider.
- Warm-up real.
- Persistência em cópia isolada.
- Rollback.

## 3. Casos obrigatórios
- Compatibilidade fetcher → scoring.
- Seleção MAIN.
- Seleção MoA.
- Fallback.
- Provedores indisponíveis.
- HTTP 429.
- Respostas inválidas.
- Modelos duplicados.
- Campos ausentes.
- Configuração inválida.
- Falha de persistência.
- Recuperação após erro.

## 4. Critérios de aprovação
- Sem falso positivo de disponibilidade.
- Sem exposição de segredos em logs.
- Persistência atômica validada.
- Rollback testado.
- Comparação com V4 documentada.
