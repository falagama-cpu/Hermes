# V5_MIGRATION_PLAN.md

## 1. Etapa 1 — Preparação
- Preservar V4.
- Documentar estado atual.
- Preparar backup e rollback.
- Não alterar configuração, cron ou runtime.

## 2. Etapa 2 — Validação isolada
- Implementar integração apenas no diretório isolado da V5.
- Testar com mocks.
- Não compartilhar arquivos mutáveis com a V4.
- Não executar chamadas externas sem autorização.

## 3. Etapa 3 — Aprovação
- Apresentar resultados.
- Listar pendências.
- Solicitar autorização explícita para testes externos.

## 4. Etapa 4 — Implantação
- Preparar troca controlada.
- Definir exatamente arquivos, jobs e configurações afetados.
- Não executar implantação nesta tarefa.

## 5. Etapa 5 — Rollback
- Documentar reativação da V4.
- Definir sinais de retorno imediato.
- Preservar histórico e evidências.

## 6. Parecer
- Reaproveitar diretamente: fetchers, normalização, persistência, warm-up, backup, reliability.
- Adaptar: scoring, seleção MAIN, seleção MoA, fallback.
- Desenvolver: integração dos contratos, testes unitários/mocks, validação controlada.
- Risco principal: substituir infraestrutura operacional por algoritmo não integrado.
- Critério de aprovação: testes reais autorizados, persistência validada, rollback testado.
