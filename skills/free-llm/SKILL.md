---
name: free-model-selection
description: "Free LLM model selection across providers via cron."
---

# Free Model Selection

## Overview
Maintaining an up-to-date list of free LLM models across **4 providers** (NVIDIA NIM, Nous Portal, OpenRouter, Cloudflare Workers AI) with cron-based selection and fallback chain configuration in `~/.hermes/profiles/pesquisa/config.yaml`.

## Scripts & What They Write

| Script | Cron schedule | Writes to config.yaml |
|--------|---------------|-----------------------|
| `update_free_models.py` | 08:00, 20:00 | `fallback_providers` only |
| `choose_best_free_llm.py` | 02:00, 14:00 | Thin wrapper → `update_free_models.py` |
| `update_models.py` | 09:00, 21:00 | `model.default`, `moa.presets.default.reference_models`, `moa.reference_models`, `moa.aggregator` |

Scripts live in `~/.hermes/profiles/pesquisa/scripts/`.

**Note**: The cron job for `update_models.py` actually runs via a wrapper script (`update_models_wrapper.sh`) that attempts to restart the gateway after a successful update. Due to gateway process constraints, the restart may fail with rc=78 when attempted from within the cron context, but the signal is still sent.

## Config Key Map
See `references/config-keys.md` for which keys each script writes and their format.

## Testing Procedure
1. Probe each provider's free model endpoint with HTTP ping (not catalog parse)
2. Filter by price == 0 / `:free` tag per provider's convention
3. Rank by context window descending
4. Build fallback chain: top N free models across all sources, deduplicated by normalized model ID
5. Report: chosen models, overlaps/conflicts between providers, any source returning 4xx/5xx

## Provider Diversity Rule
- **Minimum 2 providers** in model.default + reference_models + aggregator
- Round-robin selection: pick best model from each provider first, then fill remaining slots by context
- Never select all models from a single provider (avoids single-point-of-failure)

## Retry Policy
- **Transient errors** (429, 500, 502, 503, 504): retry with exponential backoff (2s, 4s, 8s). Max 3 retries.
- **Permanent errors** (400, 401, 403, 404, 410): log and skip immediately, no retry
- **Network errors**: same retry policy as transient

## Verification
- **NVIDIA**: probe 200 response (catalog doesn't expose pricing)
- **OpenRouter**: pricing.prompt=0 AND pricing.completion=0
- **Nous Portal**: pricing.prompt=0 AND pricing.completion=0 + probe 200
- **Cloudflare**: probe 200 response (free, no billing)

## Adding a New Provider

When integrating a new free-model provider:

### For `update_free_models.py` (writes to fallback_providers):
1. **Add provider constants** after the existing `NOUS_BASE_URL` block: `PROVIDER_CANDIDATES` list, base URL (possibly a template with env vars), and the env var name for the API key.
2. **Create a `collect_<provider>()` function** that:
   - Loads the API key via `load_key("ENV_NAME")`
   - Returns `[]` immediately if key is missing (fail-soft)
   - Probes each candidate model via `_request()`
   - **Checks response format carefully** — some providers return `{success: false, errors: [...]}` wrappers but still include a valid OpenAI-shaped body with `choices` / `id`. Verify `data.get("choices")` OR `(data.get("id") and data.get("object") == "chat.completion")` before declaring success.
   - Appends `{model, provider: "custom", base_url, key_env: "ENV_NAME"}` for each working model
3. **Call the collector in `main()`** and pass the result to `build_chain(nvidia, nous, <provider>)`.
4. **Extend `build_chain()`** to accept and add the new source (it already does for cloudflare).
5. **Test with `--check` first**, then run without it to apply.

### For `update_models.py` (writes to model.default, MOA reference models, and MOA aggregator):
1. **Add provider constants** in the appropriate section (after NVIDIA_CANDIDATES, etc.)
2. **Create a `get_<provider>_models()` function** that:
   - Loads the API key via `load_key("ENV_NAME")`
   - Returns `[]` immediately if key is missing (fail-soft)
   - Fetches the model list from the provider's API
   - Filters for free models (pricing=0 or probe 200)
   - Validates model format (text LLM, not agent/audio/etc.)
   - Returns list of dicts with `id`, `provider`, `base_url`, `context`, and optionally `key_env`
3. **Call the collector in `main()`** and add its result to the combined model list
4. **The provider diversity logic** in `pick_diverse_models()` automatically handles distribution across providers

Providers added to `update_free_models.py` appear only in `fallback_providers`.
Providers added to `update_models.py` can appear in `model.default`, `moa.reference_models`, `moa.presets.default.reference_models`, or `moa.aggregator` (subject to diversity and quality filters).

Scripts live in `~/.hermes/profiles/pesquisa/scripts/`.

**Note**: The cron job for `update_models.py` actually runs via a wrapper script (`update_models_wrapper.sh`) that attempts to restart the gateway after a successful update. Due to gateway process constraints, the restart may fail with rc=78 when attempted from within the cron context, but the signal is still sent.

## Pitfalls

### General
- **`choose-best-free-llm` and `update-free-models-14h` are redundant** — both execute `update_free_models.py`.
- **Scripts write non-overlapping but semantically linked keys**: `update_models.py` writes `model.default`, `moa.reference_models`, `moa.aggregator`; `update_free_models.py` writes `fallback_providers`. Not auto-synced — changes in one don't propagate to the other.
- **Provider inference is canonical**: All scripts use `_infer_provider()` (`nvidia/` → nvidia, `meituan/` → custom) and `_provider_base_url()`. Never hardcode `provider: openrouter`.
- **NVIDIA probe empty = fail-soft**: If `update_models.py` gets 0 NVIDIA candidates, keep the current `config.yaml` and exit 0. Do not mark cron failed for transient outages.

### Provider-Specific
- **Cloudflare response validation**: CF returns `{success: false, errors: [...]}` wrapper but the OpenAI-shaped body with `choices`/`id`/`object: "chat.completion"` is still valid. Check for `choices` or `(id + object == "chat.completion")` before declaring failure. The old token (`CLOUDFLARE_API_TOKEN`) lacked API scope — needs `CLOUDFLARE_ACCOUNT_ID` in `.env` and token with `AI Gateway:Read` scope.
- **MOA selection is NVIDIA by design**: `update_models.py` probes NVIDIA only for `model.default`, MOA reference models, and MOA aggregator. Nous/Cloudflare models appear only in `fallback_providers` via `update_free_models.py`.
- **Nous Portal requires API key**: Without `NOUS_API_KEY`, Nous appears to have 0 free models. Uses `inference-api.nousresearch.com` with Bearer auth.
- **NVIDIA free ≠ catalog pricing**: Free models identified by probing candidates at `integrate.api.nvidia.com` — the catalog doesn't expose pricing.
