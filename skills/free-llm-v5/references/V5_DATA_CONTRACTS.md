# V5_DATA_CONTRACTS.md

## 1. Objeto normalizado V4
Produzido por `enrich()` (~605) e `capabilities()` (~656):

- `_source`: provider/fonte.
- `_provider`: provider efetivo.
- `_model_id`: identificador do modelo.
- `_family`: chave de família.
- `_context`: contexto inferido/real.
- `_blob`: texto para scoring.
- `_caps`: dicionário com `coding`, `reasoning`, `agentic`, `tools`, `long_context`, `general`, `multimodal`.
- `capabilities`: mesmo conjunto, usado por V5.1.

## 2. Campos esperados pela V5.1
- `_model_id`
- `_source`
- `_family`
- `_caps.long_context`
- `capabilities` com chaves de papel (`coding`, `reasoning`, `agentic`, `tools`, `long_context`, `general`)
- `_role_score` (calculado em runtime pela V5.1)

## 3. Compatibilidade
Compatível:
- `_model_id`, `_source`, `_family`, `_caps`, `capabilities`.

Atenção:
- V5.1 também usa `_role_score`, que não existe no objeto V4 inicial; deve ser calculado antes da seleção.
- V5.1 assume pesos e campos presentes; fetchers esparsos (NVIDIA/Cloudflare) dependem de `enrich()` e `infer_sparse_metadata`.
- Diferenças de nomenclatura devem ser validadas antes da integração: V4 usa `_context`, V5.1 usa `_caps.long_context`.

## 4. Contratos por etapa

### 4.1 Coleta
Saída: lista de modelos brutos por provider.

### 4.2 Normalização
Entrada: modelo bruto + source.
Saída: objeto enriquecido com `_source`, `_provider`, `_model_id`, `_family`, `_context`, `_blob`, `_caps`, `capabilities`.

### 4.3 Scoring
Entrada: objeto normalizado.
Saída: score numérico por papel, sem alterar o objeto original, salvo `_role_score` explícito.

### 4.4 Seleção MAIN
Entrada: lista normalizada + scores.
Saída: um modelo ou `None`.

### 4.5 Seleção MoA
Entrada: lista normalizada, famílias usadas, fontes usadas.
Saída: um modelo complementar ou `None`.

### 4.6 Fallback
Entrada: lista normalizada, famílias usadas, fontes usadas.
Saída: lista ordenada de fallbacks.

### 4.7 Warm-up
Entrada: modelo selecionado.
Saída: status HTTP, latência, motivo de falha, quota.

### 4.8 Persistência
Entrada: seleção validada.
Saída: `config.yaml` atualizado atomicamente, com backup e histórico.

## 5. Riscos de contrato
- Campos ausentes em fetchers esparsos.
- Tipos inconsistentes em `supported_parameters`.
- `capabilities` com valores ausentes.
- `_role_score` não inicializado.
- Diferença entre `_context` e `_caps.long_context`.
- Modelos duplicados entre providers.
