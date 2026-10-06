# Fontes de score para o seletor (benchmarks)

Decisão: **quando vale adicionar uma fonte de score** além da Artificial Analysis (AA).
A AA é a fonte primária e o único lugar de onde sai o campo `aa` do `catalog.json`.
Qualquer outra fonte entra como complemento (cobre o que a AA não cobre), nunca como
substituta.

## Procedimento (nesta ordem)

1. **Frescor primeiro** — puxe `lastModified` pela API do HF
   (`https://huggingface.co/api/datasets/<ds>`) e/ou o máximo da coluna de data de
   submissão. Fonte parada há muitos meses não entra: o pool free troca de geração em
   semanas, então um dataset congelado tende a cobrir justamente os modelos antigos que
   já não estão no pool.
2. **Sobreposição medida, não estimada** — rode `scripts/measure_source_overlap.py`
   contra `model-selector/catalog.json` e leia o **ganho líquido** (modelos que só a nova
   fonte cobre). Decida pelo ganho líquido, nunca pela contagem bruta da fonte.
3. **Semântica do score** — `score_role` usa índices por papel (coding / agentic /
   intel). Média genérica de benchmark não mapeia para papel nenhum; se entrar, entra com
   peso menor que a AA e nunca como base do ranking.
4. **Custo** — só implemente se o ganho justificar um script novo + cache + refresh
   agendado + alteração em `score_role`.

## Candidatos conhecidos

| Fonte | Estado | O que traz | Veredito |
|---|---|---|---|
| Artificial Analysis | ativa, diária | índices por papel (coding/agentic/intel), ~688 modelos | fonte primária; já integrada |
| HF `open-llm-leaderboard/contents` | **descontinuado/congelado** (HF encerrou o leaderboard; últimas submissões em 2025-03) | `Average ⬆️` sobre 6 provas antigas (IFEval, BBH, MATH Lvl 5, GPQA, MUSR, MMLU-PRO); sem índice de coding ou agentic | não adotar |
| Deepeval | ativa | eval por execução (G-Eval, DAG, agentic task completion, arena) | não é dataset pronto — é eval com custo de tokens. Serve para pontuar os modelos que a AA não cobre, não para ranquear |

## O que o HF Leaderboard realmente contém (para não reconferir)

- Parquet único, ~1,1 MB: `https://huggingface.co/api/datasets/open-llm-leaderboard/contents/parquet/default/train`
  devolve a lista de URLs. Baixa com `urllib` + `pyarrow` — **não precisa da lib `datasets`**
  (num venv de scratch: `uv venv .venv --python 3.12 && uv pip install pyarrow`).
- 4.576 linhas, 36 colunas. Úteis: `fullname`, `Average ⬆️`, `IFEval`, `BBH`,
  `MATH Lvl 5`, `GPQA`, `MUSR`, `MMLU-PRO`, `#Params (B)`, `Architecture`,
  `Hub License`, `Submission Date`, `Upload To Hub Date`.
- O nome da coluna de média **inclui o emoji** (`Average ⬆️`): use o nome exato, `df['Average']` não existe.
- `Average ⬆️` é a média dessas 6 provas, não um índice de capacidade geral.

## Matching de nomes (aproxima o `aa_scores.py`)

Nome do provedor e nome do dataset chegam em ordens diferentes. Tokenize o slug, remova o
prefixo do criador e um conjunto de tokens de ruído, e exija ≥2 tokens compartilhados **e**
≥50% dos tokens do nome do pool presentes no nome do dataset. Ruído: `instruct it chat fp8
fast bf16 fp16 awq gptq gguf int4 int8 quantized hf v0 v1 free preview high low max xhigh
reasoning base distill` mais tokens que são só versão (`v3`, `2.5`). `flash` / `mini` /
`lite` **não** são ruído — são outros modelos.

O matcher é conservador de propósito: nomes com menos de 2 tokens úteis nunca casam, o que
subestima a cobertura em vez de superestimá-la. Ao reportar cobertura, diga que é um piso.
