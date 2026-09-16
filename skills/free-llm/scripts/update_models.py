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
  - provider_models_cache.json           (TODOS os free: nvidia + nós)

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

# ─── NVIDIA NIM: candidatos a sondar (previews free) ──────────────────────────

NVIDIA_CANDIDATES = [
    "minimaxai/minimax-m3",
    "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek-ai/deepseek-v4-pro-0813",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-nano-3-30b-a3b",
    "moonshotai/kimi-k2.6",
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

# ─── Nous Portal (catálogo — validação, não ranking) ──────────────────────────

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
    for m in text_llms:
        if m["id"] in skip:
            continue
        if any(p in m["id"] for p in SKIP_AGG_EXTRA):
            continue
        if not is_high_priority(m["id"]):
            continue
        return m["id"]
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
    new_preset = (
        "      reference_models:\n"
        + "".join(
            f"        - provider: openrouter\n          model: {m}\n          enabled: true\n"
            for m in ref_models
        )
    )
    config_text = re.sub(
        r"(  presets:\n    default:\n)"
        r"      reference_models:\n"
        r"(?:        - provider: \S+\n          model: .*\n(?:          enabled: (?:true|false)\n)?)+",
        r"\1" + new_preset,
        config_text,
    )
    new_root = (
        "  reference_models:\n"
        + "".join(
            f"    - provider: openrouter\n      model: {m}\n      enabled: true\n"
            for m in ref_models
        )
    )
    config_text = re.sub(
        r"  reference_models:\n"
        r"(?:    - provider: \S+\n      model: .*\n(?:      enabled: (?:true|false)\n)?)+",
        new_root + "\n",
        config_text,
    )
    return config_text

def patch_moa_aggregator(config_text, agg_model):
    config_text = re.sub(
        r"(      aggregator:\n        provider: openrouter\n        model: ).*",
        rf"\g<1>{agg_model}",
        config_text,
    )
    config_text = re.sub(
        r"(  aggregator:\n    provider: openrouter\n    model: ).*",
        rf"\g<1>{agg_model}",
        config_text,
    )
    return config_text

def patch_model_default(config_text, default_model):
    new_block = (
        "model:\n"
        f"  default: {default_model}\n"
        "  provider: openrouter\n"
        f"  model: {default_model}\n"
    )
    config_text = re.sub(
        r"model:\n  default: [^\n]+\n  provider: [^\n]+\n  model: [^\n]+\n",
        new_block,
        config_text,
    )
    return config_text

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"{LOG_PREFIX} iniciando atualização por ranking (2 fontes: nvidia/nous)...")
    print(f"{LOG_PREFIX} HERMES_HOME: {HERMES_HOME}")

    nvidia_models = get_nvidia_models()
    if not nvidia_models:
        print(f"{LOG_PREFIX} ERRO: sem modelos free", file=sys.stderr)
        sys.exit(1)

    text_llms = [m for m in nvidia_models if is_text_llm(m["id"])]

    print(f"{LOG_PREFIX} {len(nvidia_models)} free, {len(text_llms)} LLMs de texto — nvidia previews:")
    for i, m in enumerate(nvidia_models, 1):
        tag = "LLM" if is_text_llm(m["id"]) else "---"
        prio = "" if is_high_priority(m["id"]) else " (baixa prioridade p/ default)"
        print(f"  {i:2d} [{tag}] {m['id']:<48}{prio}")

    nous_ids = get_nous_catalog_ids()
    update_provider_cache(nvidia_models, nous_ids)

    high_priority_llms = [m for m in text_llms if is_high_priority(m["id"])]
    default_pool = high_priority_llms or text_llms  # fallback se tudo for low-priority
    default_model = default_pool[0]["id"] if default_pool else "deepseek-ai/deepseek-v4-flash-0731"
    ref_models    = pick_by_ranking(text_llms, n=2, skip=(default_model,))
    agg_model     = pick_aggregator(text_llms, skip=(default_model, *ref_models))

    print(f"{LOG_PREFIX} model.default:    {default_model}")
    print(f"{LOG_PREFIX} reference_models: {ref_models}")
    print(f"{LOG_PREFIX} aggregator:       {agg_model}")

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

    # Reinicia o gateway para que as novas seleções de modelo entrem em efeito
    print("[acao] reiniciando gateway para assumir novos modelos...")
    import subprocess
    subprocess.run(["hermes", "gateway", "restart"], capture_output=True, timeout=30)

    print(f"{LOG_PREFIX} concluído.")

if __name__ == "__main__":
    main()