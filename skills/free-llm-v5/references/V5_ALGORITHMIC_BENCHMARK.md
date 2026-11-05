# V5_ALGORITHMIC_BENCHMARK.md

## 1. Objetivo
Comparar, de forma offline e determinística, as decisões de seleção da V4 e da V5.1 sobre exatamente o mesmo conjunto de modelos, sem chamadas externas, sem alterar V4 e sem persistir seleção.

## 2. Escopo
- Entrada: conjunto fixo e versionado de modelos normalizados.
- Saída: relatório de comparação de MAIN, MoA, fallbacks, scores e diversidade de provedores.
- Proibido: rede, credenciais, config.yaml, cron, Gateway, TUI, runtime MoA.

## 3. Entradas
- Modelos de teste já existentes em `hermes-free-model-selector-v5.1.py` (linhas 164-231).
- Fixtures adicionais normalizadas conforme `V5_DATA_CONTRACTS.md`.
- Objetos devem conter `_model_id`, `_source`, `_family`, `_caps`, `capabilities`.

## 4. Comparação
### V4
- `choose_main` (~1939)
- `choose_moa` (~1982)
- `choose_fallbacks` (~2146)

### V5.1
- `choose_main_v5` (~327)
- `choose_moa` (~472)
- `choose_fallbacks` (~487)

Observação: nomes coincidem em parte, mas as implementações diferem. Os módulos devem ser importados separadamente e as funções chamadas explicitamente.

## 5. Métricas
- MAIN selecionado por cada versão.
- Score do MAIN.
- Fonte do MAIN.
- MoA selecionado.
- Complementaridade MoA vs MAIN.
- Fallbacks selecionados.
- Diversidade de fontes.
- Diversidade de famílias.
- Diferença de score.
- Casos em que V5.1 altera a escolha da V4.

## 6. Critérios de interpretação
- Diferença de escolha não é, por si só, melhoria.
- Melhoria exige critério definido antes da execução.
- O benchmark mede decisão, não disponibilidade real.
- Resultados de mock não comprovam comportamento em produção.

## 7. Limitações
- Sem catálogo real.
- Sem warm-up real.
- Sem persistência.
- Sem validação de HTTP 429, 401, 403, 404 ou 5xx.
- Sem integração com perfil `pesquisa`.

## 8. Próximo passo recomendado
Criar um harness offline versionado, com fixtures fixas e saída em JSON/Markdown, antes de qualquer integração operacional.
