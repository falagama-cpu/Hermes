# Scripts legados (não agendados)

`update_models.py`, `update_free_models.py`, `choose_best_free_llm.py` e `update_models_wrapper.sh` ficam em `scripts/` só para referência/rollback. Desde 2026-10-01 o único dono do `model.default` é o seletor v4; o job `update-hermes-models` (09/21h, `update_models.py`) está **pausado** no perfil pesquisa porque sobrescrevia o MAIN do v4 com NVIDIA.

Reativar só se abandonar o v4: `hermes cron resume <id>` — e então pause o `choose-best-free-llm`, nunca os dois ativos.

## update_models.py (MAIN NVIDIA + MoA)
- Descobre `/models` da NVIDIA (filtro `SKIP_AGENT`), prioriza `NVIDIA_CANDIDATES`, sonda até `NVIDIA_PROBE_CAP` (14); vivo = 200/429.
- Grava `model.default` (NVIDIA text-only), `moa.reference_models` (preset + root), `moa.aggregator` (multimodal preferencial: NIM `MULTIMODAL_AGG_CANDIDATES_NIM`, depois OpenRouter `MULTIMODAL_AGG_CANDIDATES_OR` com `:free`, probe 200 ou 400).
- Patch YAML por **regex** (ancorado em `aggregator: &id001`, indentação 8 espaços) — se o layout do config mudar falha em silêncio.
- `assert_free_models()` aborta exit 2 se algum selecionado tiver prefixo pago.
- Reconciliação de órfãos: só remove quem sumiu do `/models`; timeout/429 não remove.
- Restart: `gateway_unit_name()` só usa `hermes-gateway-<perfil>.service` se `is-enabled`=enabled; senão `hermes-gateway.service` (reiniciar unit desabilitado causou loop de 2358 restarts, exit 75).
- Precisa `PYTHONPATH=scripts/` (wrapper exporta) por causa do `requests/` local.

## update_free_models.py (cadeia NVIDIA→OpenRouter→Nous→Cloudflare)
- Dedupe por id normalizado (`split(':')[0].lower()`), promove `chain[0]` a MAIN/MoA, `fallback_providers = chain[1:]`.
- Dead tracker binário `provider_models_dead_tracker.json` (3 falhas seguidas → pula).
- Cloudflare: base_url OpenAI-compatível `.../ai/v1`, sondar `POST {base_url}/chat/completions` — nunca `.../ai/run/{modelo}`.
- `choose_best_free_llm.py` é só wrapper dele.

## Armadilhas que valeram para ambos
- Duas cópias dos scripts: a da skill e a do perfil (`~/.hermes/profiles/<p>/scripts/`). O cron roda a do perfil.
- NVIDIA NIM: limite ~40 rpm na conta; HTTP 0 = timeout, não conclua que o modelo morreu.
- `model.default: default` literal = script não completou.
