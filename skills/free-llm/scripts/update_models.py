#!/usr/bin/env python3
"""
update_models.py — atualiza modelos free no config.yaml do Hermes

2 provedores free consultados (OpenRouter removido do cron):
  - NVIDIA NIM  (fonte principal de candidatos — previews free, identificados
                 por probe 200 em integrate.api.nvidia.com; o /v1/models não
                 expõe contexto, então o ranking é por probe)
  - Nous Portal (catálogo em hermes-agent.nousresearch.com/docs/api/
                 model-catalog.json — validação/catálogo; como os IDs Nous já
                 constam do NVIDIA e o Nous é roteado como custom+key_env,
                 entra como VALIDAÇÃO, não como fonte de ranking paralela)

Critério de ranking: modelos que respondem probe 200 (NVIDIA previews).
Filtro de qualidade impede variantes small/mini/preview/lite de virarem
model.default ou aggregator (podem aparecer como reference_model secundário).

Atualiza:
  - model.default                        (modelo #1 free por ranking, filtrado)
  - moa.presets.default.reference_models (top 2 free, filtrado)
  - moa.reference_models                 (mesmos 2, formato root)
  - moa.aggregator                       (próximo não-skip, filtrado)
  - provider_models_cache.json           (TODOS os free: nvidia + nous)

Reconciliação NVIDIA: qualquer entrada `provider: nvidia` em
moa.reference_models (raiz ou preset) cujo `model` não exista mais na
lista atual da NIM é removida — a NIM não fica órfã silenciosamente.
"""

import hashlib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes/profiles/pesquisa"))
CONFIG_PATH = HERMES_HOME / "config.yaml"
CACHE_PATH  = HERMES_HOME / "provider_models_cache.json"

LOG_PREFIX = f"[update_models {datetime.now().strftime('%Y-%m-%d %H:%M')}]"


def load_key(env_name: str) -> Optional[str]:
    """Load API key from environment or .env file."""
    k = os.environ.get(env_name, "").strip()
    if k:
        return k
    env_path = HERMES_HOME / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{env_name}="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except OSError:
        pass
    return None

# ─── Helpers ──────────────────────────────────────────────────────────────────

def fetch_json(url, headers=None, timeout=20):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept":     "application/json",
        **(headers or {}),
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"{LOG_PREFIX} WARN fetch {url}: {e}", file=sys.stderr)
        return None

def read_config():
    return CONFIG_PATH.read_text()

def write_config(text):
    backup = CONFIG_PATH.with_suffix(".yaml.pre-update-models")
    backup.write_text(read_config())
    CONFIG_PATH.write_text(text)

# ─── Padrões para filtrar modelos não-LLM de texto ────────────────────────────

SKIP_AGENT = [
    "content-safety", "openrouter/free", "lyria", "rerank",
    "embed", "tts", "flux-tts", "s2.1-pro", "whisper",
    "fish-audio", "deepgram", "clip-preview",
]
SKIP_AGG_EXTRA = ["nano", "small", "mini", "liquid", "lfm", "ling"]

# Modelos "small/mini/preview/lite/..." costumam anunciar contexto alto mas
# são variantes reduzidas — não servem como model.default nem aggregator
# (podem aparecer como reference_model secundário). Filtro de qualidade
# aplicado SÓ na escolha de default/aggregator; o ranking/cache continua
# com a lista completa (sem esse filtro).
LOW_PRIORITY_MARKERS = ["small", "mini", "preview", "lite", "experimental", "beta", "alpha"]

def is_text_llm(model_id: str) -> bool:
    return not any(p in model_id for p in SKIP_AGENT)

def is_high_priority(model_id: str) -> bool:
    low = model_id.lower()
    return not any(marker in low for marker in LOW_PRIORITY_MARKERS)

# Multimodal models allowed ONLY for aggregator (not model.default / reference_models)
# NIM candidates (probed via integrate.api.nvidia.com)
MULTIMODAL_AGG_CANDIDATES_NIM = [
    "moonshotai/kimi-k3",
    "z-ai/glm-5-3-flash",
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "meta/muse-glimmer-30b",
]
# OpenRouter candidates (probed via openrouter.ai — `:free` suffix required)
# ordered by preference
MULTIMODAL_AGG_CANDIDATES_OR = [
    "deepseek-ai/deepseek-v4.1-flash:free",
    "z-ai/glm-5-3-flash:free",
    "moonshotai/kimi-k3:free",
]
# unified set for is_multimodal_agg_candidate check (strip :free for normalisation)
MULTIMODAL_AGG_CANDIDATES = MULTIMODAL_AGG_CANDIDATES_NIM + MULTIMODAL_AGG_CANDIDATES_OR

def is_multimodal_agg_candidate(model_id: str) -> bool:
    """Check if model is a multimodal candidate suitable for aggregator."""
    return model_id in MULTIMODAL_AGG_CANDIDATES

# ─── NVIDIA NIM: candidatos a sondar (previews free) ──────────────────────────

NVIDIA_CANDIDATES = [
    # Text-only high-priority (para model.default + reference_models)
    "minimaxai/minimax-m3",
    "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek-ai/deepseek-v4-pro-0813",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-nano-3-30b-a3b",
    "moonshotai/kimi-k2.6",
    "nvidia/nemotron-3.5-lightning",
    "nvidia/nemotron-3-mini-4b",
    "nvidia/llama-3.1-nemotron-70b-instruct",
    "nvidia/nemotron-4-mini-hindi-4b-instruct",
    "nvidia/mistral-nemo-minitron-8b-8k-instruct",
    # Multimodal MoE (apenas para aggregator)
    "moonshotai/kimi-k3",
    "z-ai/glm-5-3-flash",
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    "meta/muse-glimmer-30b",
]

def get_nvidia_models():
    """
    NVIDIA /v1/models não expõe preço. Sondamos os candidatos e ficamos com
    os que respondem 200. A lista de candidatos é mantida à mão (previews free).
    """
    key = load_key("NVIDIA_API_KEY")
    if not key:
        return []

    import requests
    out = []
    for mid in NVIDIA_CANDIDATES:
        try:
            r = requests.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={"model": mid, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
                timeout=20,
            )
            if r.status_code == 200:
                out.append({"id": mid, "name": mid, "ctx": 0})
        except Exception:
            pass
    return out

# Separate text-only and multimodal models for proper selection
def filter_text_only_models(models):
    """Filter to only text-only models (exclude multimodal)."""
    multimodal_ids = set(MULTIMODAL_AGG_CANDIDATES)
    return [m for m in models if m["id"] not in multimodal_ids]

def filter_multimodal_models(models):
    """Filter to only multimodal models (for aggregator)."""
    multimodal_ids = set(MULTIMODAL_AGG_CANDIDATES)
    return [m for m in models if m["id"] in multimodal_ids]

# ─── Nous Portal (catálogo — validação, não ranking) ──────────────────────────

def get_openrouter_multimodal_models():
    """
    Sonda candidatos multimodal MoE no OpenRouter (endpoint free).
    Retorna lista de dicts {id, name, ctx} para os que respondem 200.
    IDs incluem sufixo ':free' para routing correto no OpenRouter.
    """
    key = load_key("OPENROUTER_API_KEY")
    if not key:
        return []

    import requests
    out = []
    for mid in MULTIMODAL_AGG_CANDIDATES_OR:
        try:
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
                },
                json={"model": mid, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                timeout=20,
            )
            if r.status_code in (200, 400):  # 400 = model exists but rejects text-only (multimodal)
                out.append({"id": mid, "name": mid, "ctx": 0})
                status_note = "OK" if r.status_code == 200 else "available (multimodal-only)"
                print(f"{LOG_PREFIX} OR multimodal {status_note}: {mid}")
            else:
                print(f"{LOG_PREFIX} OR multimodal {mid}: {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"{LOG_PREFIX} OR multimodal {mid}: {e}", file=sys.stderr)
    return out


def get_nous_catalog_ids():
    """
    Catálogo oficial da Nous Portal. Os IDs listados aqui existem no catálogo
    NVIDIA (mesmo backend), então a Nous entra como VALIDAÇÃO/catálogo,
    não como uma fonte de ranking paralela.
    """
    data = fetch_json("https://hermes-agent.nousresearch.com/docs/api/model-catalog.json")
    if not data:
        return []
    nous = data.get("providers", {}).get("nous", {})
    return [m["id"] for m in nous.get("models", [])]


# Lista de modelos Nous free conhecidos (verificados por probe)
NOUS_FREE_CANDIDATES = [
    "meituan/longcat-2.0:free",
    "meituan/longcat-2.1:free",
    "meituan/longcat-3:free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "qwen/qwen3-8b:free",
    "qwen/qwen3-14b:free",
    "qwen/qwen3-32b:free",
    "qwen/qwen3-235b-a22b:free",
    "deepseek-ai/deepseek-r1:free",
    "deepseek-ai/deepseek-v3:free",
    "meta-llama/llama-3.1-8b-instruct:free",
    "meta-llama/llama-3.1-70b-instruct:free",
    "meta-llama/llama-3.2-1b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemma-2-9b-it:free",
    "google/gemma-2-27b-it:free",
    "microsoft/phi-3-medium-128k-instruct:free",
    "microsoft/phi-3-mini-128k-instruct:free",
]


def get_nous_free_models():
    """
    Retorna modelos Nous free validados por probe (200 response).
    Usa lista conhecida de candidatos, similar ao NVIDIA.
    """
    key = load_key("NOUS_API_KEY")
    if not key:
        return []

    import requests
    out = []
    for mid in NOUS_FREE_CANDIDATES:
        try:
            r = requests.post(
                "https://inference-api.nousresearch.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={"model": mid, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
                timeout=20,
            )
            if r.status_code == 200:
                out.append(mid)
        except Exception:
            pass
    return out

# ─── Seleciona por ranking ────────────────────────────────────────────────────

def pick_by_ranking(text_llms, n, skip=()):
    out = []
    for m in text_llms:
        if m["id"] in skip:
            continue
        if not is_high_priority(m["id"]):
            continue
        out.append(m["id"])
        if len(out) >= n:
            break
    return out

def pick_aggregator(text_llms, skip=()):
    # Primeiro tenta multimodal candidates (alta prioridade para aggregator)
    for m in text_llms:
        if m["id"] in skip:
            continue
        if is_multimodal_agg_candidate(m["id"]):
            return m["id"]
    # Fallback: text-only high-priority
    for m in text_llms:
        if m["id"] in skip:
            continue
        if any(p in m["id"] for p in SKIP_AGG_EXTRA):
            continue
        if not is_high_priority(m["id"]):
            continue
        return m["id"]
    # Último recurso: qualquer text_llm não usado
    for m in text_llms:
        if m["id"] not in skip:
            return m["id"]
    return "deepseek-ai/deepseek-v4-flash-0731"

# ─── Cache (TODOS os free, 2 fontes) ──────────────────────────────────────────

def update_provider_cache(nvidia_models, nous_ids):
    try:
        cache = json.loads(CACHE_PATH.read_text()) if CACHE_PATH.exists() else {}
    except Exception:
        cache = {}

    all_nv_ids = [m["id"] for m in nvidia_models]

    fp = hashlib.md5("|".join(sorted(all_nv_ids)).encode()).hexdigest()[:16]
    cache["nvidia"] = {"fp": fp, "at": time.time(), "models": all_nv_ids}
    if nous_ids:
        cache["nous"] = {
            "fp":     hashlib.md5("|".join(sorted(nous_ids)).encode()).hexdigest()[:16],
            "at":     time.time(),
            "models": nous_ids,
        }
    CACHE_PATH.write_text(json.dumps(cache, indent=2))

    text_llms = [m for m in nvidia_models if is_text_llm(m["id"])]
    outros    = [m for m in nvidia_models if not is_text_llm(m["id"])]
    print(f"{LOG_PREFIX} cache: {len(all_nv_ids)} free nvidia "
          f"({len(text_llms)} LLMs texto, {len(outros)} outros) | "
          f"{len(nous_ids)} nous")

# ─── Reconciliação de entradas NVIDIA órfãs no moa.reference_models ───────────

def find_orphan_nvidia_models(config_text, nvidia_ids_current):
    """
    Acha modelos com `provider: nvidia` no config que NÃO estão mais na
    lista atual da NIM. nvidia_ids_current vazio (sem NVIDIA_API_KEY ou
    fetch falhou) não aciona reconciliação — evita apagar por engano
    numa falha transitória de rede.
    """
    if not nvidia_ids_current:
        return set()
    orphans = set()
    for m in re.finditer(r"provider: nvidia\n\s*model: (\S+)", config_text):
        model_id = m.group(1)
        if model_id not in nvidia_ids_current:
            orphans.add(model_id)
    return orphans

def strip_orphan_nvidia_entries(config_text, orphans):
    """
    Remove blocos `- provider: nvidia / model: <orphan> / enabled: ...`
    inteiros do config (preset e root), para qualquer indentação usada
    em moa.reference_models. Deixa o resto do arquivo intacto.
    """
    if not orphans:
        return config_text, False
    changed = False
    for model_id in orphans:
        pattern = re.compile(
            r"[ \t]*- provider: nvidia\n"
            r"[ \t]*model: " + re.escape(model_id) + r"\n"
            r"(?:[ \t]*enabled: (?:true|false)\n)?"
        )
        new_text, n = pattern.subn("", config_text)
        if n:
            config_text = new_text
            changed = True
            print(f"{LOG_PREFIX} removido modelo NVIDIA órfão (saiu da lista NIM): {model_id}")
    return config_text, changed

# ─── Patchers YAML ────────────────────────────────────────────────────────────

def patch_moa_reference_models(config_text, ref_models):
    """
    Atualiza reference_models no preset e root.
    Apenas modelos NVIDIA (provider: custom com key_env não funciona no MOA).
    """
    # Preset block: 6-space indent for entries
    new_preset = (
        "  presets:\n    default:\n      reference_models:\n"
        + "".join(
            f"      - provider: nvidia\n        model: {m}\n        base_url: https://integrate.api.nvidia.com/v1\n        enabled: true\n"
            for m in ref_models
        )
    )
    config_text = re.sub(
        r"  presets:\n    default:\n      reference_models:\n(?:      - provider: \S+\n        model: \S+\n(?:        base_url: \S+\n)?        enabled: (?:true|false)\n)+",
        new_preset,
        config_text,
    )

    # Root block: 4-space indent for entries
    new_root = (
        "  reference_models:\n"
        + "".join(
            f"  - provider: nvidia\n    model: {m}\n    base_url: https://integrate.api.nvidia.com/v1\n    enabled: true\n"
            for m in ref_models
        )
    )
    config_text = re.sub(
        r"  reference_models:\n(?:  - provider: \S+\n    model: \S+\n(?:    base_url: \S+\n)?    enabled: (?:true|false)\n)+",
        new_root + "\n",
        config_text,
    )
    return config_text

def patch_moa_aggregator(config_text, agg_model):
    # Determine provider from model ID
    # Models with :free suffix are OpenRouter
    if agg_model.endswith(":free"):
        provider = "openrouter"
        base_url = "https://openrouter.ai/api/v1"
    elif agg_model.startswith("nvidia/"):
        provider = "nvidia"
        base_url = "https://integrate.api.nvidia.com/v1"
    elif agg_model.startswith(("moonshotai/", "z-ai/", "deepseek-ai/", "meta/", "minimaxai/")):
        provider = "nvidia"  # via NIM
        base_url = "https://integrate.api.nvidia.com/v1"
    else:
        provider = "nvidia"
        base_url = "https://integrate.api.nvidia.com/v1"
    
    # Preset block (8-space indent for provider/model under presets.default.aggregator)
    config_text = re.sub(
        r"(      aggregator: &id001\n        provider: )\S+(\n        model: )\S+",
        rf"\g<1>{provider}\g<2>{agg_model}",
        config_text,
    )
    config_text = re.sub(
        r"(      aggregator: &id001\n        provider: \S+\n        model: \S+\n        base_url: )\S+",
        rf"\g<1>{base_url}",
        config_text,
    )
    
    # Root block (2-space indent for provider/model under moa.aggregator)
    config_text = re.sub(
        r"(  aggregator: \*id001\n  reference_temperature: )\S+",
        r"\g<1>0.6",  # keep reference_temperature
        config_text,
    )
    # The root aggregator is a YAML anchor reference (*id001), so we don't change it directly
    # It's updated via the preset block above
    
    return config_text

def patch_model_default(config_text, default_model):
    # Substitui o bloco model: { default, provider, base_url, [api_mode] }
    # A estrutura real do config NÃO tem linha "model: X" dentro do bloco —
    # apenas default, provider, base_url e opcionalmente api_mode.
    new_block = (
        "model:\n"
        f"  default: {default_model}\n"
        "  provider: nvidia\n"
        "  base_url: https://integrate.api.nvidia.com/v1\n"
    )
    # Cobre estruturas com ou sem api_mode após base_url
    config_text = re.sub(
        r"model:\n  default: [^\n]+\n  provider: [^\n]+\n(?:  model: [^\n]+\n)?(?:  base_url: [^\n]+\n)?(?:  api_mode: [^\n]+\n)?",
        new_block,
        config_text,
        count=1,
    )
    return config_text

# ─── Guarda: garante que nenhum modelo pago entre no config ───────────────────

# Prefixos de provedores pagos que NUNCA devem aparecer como default/refs/aggregator
PAID_PREFIXES = ["anthropic/", "openai/", "google/gemini", "cohere/", "mistralai/command"]

def assert_free_models(*model_ids):
    """
    Aborta se qualquer modelo selecionado pertencer a um provedor pago.
    Proteção contra bugs de fallback que possam introduzir custo inesperado.
    """
    for mid in model_ids:
        if mid and any(mid.startswith(p) for p in PAID_PREFIXES):
            print(f"{LOG_PREFIX} ABORT: modelo pago detectado na seleção: {mid}", file=sys.stderr)
            print(f"{LOG_PREFIX} Nenhuma alteração feita no config.yaml.", file=sys.stderr)
            sys.exit(2)

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"{LOG_PREFIX} iniciando atualização por ranking (2 fontes: nvidia/nous)...")
    print(f"{LOG_PREFIX} HERMES_HOME: {HERMES_HOME}")

    nvidia_models = get_nvidia_models()
    if not nvidia_models:
        print(f"{LOG_PREFIX} ERRO: sem modelos free", file=sys.stderr)
        sys.exit(1)

    # Separate text-only and multimodal models
    text_only_models = filter_text_only_models(nvidia_models)
    multimodal_models = filter_multimodal_models(nvidia_models)

    # Also probe OpenRouter for multimodal MoE candidates not on NIM
    or_multimodal_models = get_openrouter_multimodal_models()
    # Combine: NIM multimodal first (preferred, lower latency), then OR
    all_multimodal_models = multimodal_models + [
        m for m in or_multimodal_models if m["id"] not in {x["id"] for x in multimodal_models}
    ]
    
    # text_llms for model.default and reference_models (text-only only)
    text_llms = [m for m in text_only_models if is_text_llm(m["id"])]

    print(f"{LOG_PREFIX} {len(nvidia_models)} free, {len(text_only_models)} text-only, {len(multimodal_models)} multimodal — nvidia previews:")
    for i, m in enumerate(nvidia_models, 1):
        if m["id"] in set(MULTIMODAL_AGG_CANDIDATES):
            tag = "MULTIMODAL"
        elif is_text_llm(m["id"]):
            tag = "LLM"
        else:
            tag = "---"
        prio = "" if is_high_priority(m["id"]) else " (baixa prioridade p/ default)"
        print(f"  {i:2d} [{tag}] {m['id']:<48}{prio}")

    nous_ids = get_nous_catalog_ids()
    update_provider_cache(nvidia_models, nous_ids)

    high_priority_llms = [m for m in text_llms if is_high_priority(m["id"])]
    default_pool = high_priority_llms or text_llms  # fallback se tudo for low-priority
    default_model = default_pool[0]["id"] if default_pool else "deepseek-ai/deepseek-v4-flash-0731"
    ref_models    = pick_by_ranking(text_llms, n=2, skip=(default_model,))
    # Aggregator: prefer multimodal (NIM first, then OR), then text-only
    agg_model     = pick_aggregator(all_multimodal_models + text_llms, skip=(default_model, *ref_models))

    # Nota: modelos Nous ficam apenas em fallback_providers (o MOA reference_models
    # não autentica provider: custom com key_env corretamente)
    print(f"{LOG_PREFIX} model.default:    {default_model}")
    print(f"{LOG_PREFIX} reference_models: {ref_models}")
    print(f"{LOG_PREFIX} aggregator:       {agg_model}")

    # Guarda: aborta se qualquer modelo selecionado for pago
    assert_free_models(default_model, *ref_models, agg_model)

    config_text = read_config()
    original    = config_text

    # reconciliação primeiro (remove NVIDIA órfão antes de reescrever os slots)
    orphans = find_orphan_nvidia_models(config_text, [m["id"] for m in nvidia_models])
    config_text, _ = strip_orphan_nvidia_entries(config_text, orphans)

    config_text = patch_model_default(config_text, default_model)
    config_text = patch_moa_reference_models(config_text, ref_models)
    config_text = patch_moa_aggregator(config_text, agg_model)

    if config_text == original:
        print(f"{LOG_PREFIX} nenhuma mudança no config.yaml")
    else:
        write_config(config_text)
        print(f"{LOG_PREFIX} config.yaml atualizado!")

    # Reinicia o gateway (host gateway via systemd, não por perfil)
    print("[acao] reiniciando gateway para assumir novos modelos...")
    import subprocess
    # Tenta systemctl --user primeiro (mais confiável no cron)
    r = subprocess.run(
        ["systemctl", "--user", "restart", "hermes-gateway.service"],
        capture_output=True, timeout=30,
    )
    if r.returncode != 0:
        # Fallback: hermes gateway restart com HERMES_HOME do host
        import os
        env = {**os.environ, "HERMES_HOME": str(Path.home() / ".hermes")}
        subprocess.run(["hermes", "gateway", "restart"], capture_output=True, timeout=30, env=env)

    print(f"{LOG_PREFIX} concluído.")

if __name__ == "__main__":
    main()
