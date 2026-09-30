#!/usr/bin/env python3
"""
update_free_models.py — mantém a lista de modelos FREE de 4 provedores
(OpenRouter, NVIDIA, Nous, Cloudflare) atualizada no config.yaml do perfil <perfil>.

PROBLEMA que resolve: os provedores trocam os modelos gratuitos com frequência.
Este script cobre OpenRouter, NVIDIA NIM, Nous Portal e Cloudflare Workers AI.

As fontes e como cada uma informa "gratuito":
  - OpenRouter : /models expõe modelos :free e/ou pricing 0; validamos via
                 /chat/completions com OPENROUTER_API_KEY.
  - NVIDIA NIM : /models NÃO expõe preço. Os free são os "previews" que rodam
                 via integrate.api.nvidia.com sem billing. Sondamos uma lista
                 conhecida de candidatos e ficamos com os que respondem 200.
  - Nous       : /models (inference-api.nousresearch.com) devolve :free com
                 pricing 0. Autentica por NOUS_API_KEY (Bearer) — SEM OAuth.
                 No config, o provider "nous" do Hermes exige OAuth device code,
                 então roteamos como provider "custom" + key_env, contornando.

Regras de escrita (idempotente):
  - fallback_providers = cadeia ordenada (nvidia como provider
    nativo; nous como provider "custom" + key_env NOUS_API_KEY).
  - Se nada mudou, não reescreve o arquivo. Reinicia o gateway do perfil ao final de toda execução (exceto --check).

Uso:
  python3 update_free_models.py           # roda e aplica
  python3 update_free_models.py --check   # só imprime o que faria
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes/profiles/<perfil>"))
ENV_PATH = HERMES_HOME / ".env"
CONFIG_YAML = HERMES_HOME / "config.yaml"

TOP_N = 10                # quantos fallbacks compõem a cadeia final
PROBE_CAP = 4             # quantos candidatos por fonte sao sondados (ping real)
MAX_RETRIES = 0           # sem retry: modelo free instavel/lento e melhor pular
BASE_DELAY = 1.0
TIMEOUT = 30              # probe; nemotron 1M :free leva ~20s p/ iniciar stream

PERMANENT = {400, 401, 402, 403, 404, 410, 422}
TRANSIENT = {408, 425, 429, 500, 502, 503, 504, 522, 524}

# modelos que não servem de LLM-texto para agente/cron
SKIP_AGENT = [
    "content-safety", "note-preview", "lyria", "rerank", "embed",
    "tts", "flux-tts", "whisper", "fish-audio", "deepgram",
    "clip-preview", "nano-omni", "ling-3.0-flash-sante",
    "ling-3.0-flash-fin", "thinkingmachines",  # thinkingmachines = agentic-only (403)
    "cosmos", "detector", "speaker", "ising", "kumo", "riva",
    "voicechat", "diffusiongemma", "transfer",
]

# NVIDIA NIM: /models lista o catálogo (sem preço). Descobrimos por lá e usamos
# esta lista só como PRIORIDADE; o probe filtra o que não é free (402/403).
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_PREFERRED = [
    "moonshotai/kimi-k3",
    "z-ai/glm-5-3",
    "deepseek-ai/deepseek-v4.1-flash",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",
    "z-ai/glm-5-3-flash",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
]
NVIDIA_PROBE_CAP = 6

# OpenRouter: modelos free via catálogo /models + probe OpenAI-compatible
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Substrings de id em ordem de preferência (ids conferidos no /models).
OPENROUTER_PREFERRED = ["nemotron-3-super", "laguna-xs-2.1", "laguna-s-2.1",
                        "gemma-4-31b", "nemotron-3-ultra", "gemma-4-26b"]
OPENROUTER_PROBE_CAP = 8


def _or_rank(c: dict) -> tuple[int, int]:
    for i, frag in enumerate(OPENROUTER_PREFERRED):
        if frag in c["model"].lower():
            return (i, -c["context"])
    return (len(OPENROUTER_PREFERRED), -c["context"])


# nós free: rotear como custom (provider nativo "nous" exige OAuth)
NOUS_BASE_URL = "https://inference-api.nousresearch.com/v1"

# Cloudflare AI: modelos free via Workers AI
CLOUDFLARE_ACCOUNT_ID = "<CLOUDFLARE_ACCOUNT_ID>"
CLOUDFLARE_BASE_URL = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1"
CLOUDFLARE_CANDIDATES = [
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
    "@cf/meta/llama-3.2-1b-instruct",
    "@cf/meta/llama-3.2-3b-instruct",
    "@cf/meta/llama-4-scout-17b-16e-instruct",
    "@cf/google/gemma-7b-it-lora",
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
    "@cf/mistralai/mistral-small-3.1-24b-instruct",
]


def load_key(env_name: str) -> Optional[str]:
    k = os.environ.get(env_name, "").strip()
    if k:
        return k
    try:
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{env_name}="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    except OSError:
        pass
    return None


def _request(url: str, key: str, *, body: Optional[dict] = None, timeout: int = TIMEOUT) -> tuple[int, str]:
    """Faz request HTTP com timeout REAL (via requests, que respeita o tempo
    total; urllib.urlopen não corta stream pendurado em provider lento)."""
    import requests

    headers = {
        "Authorization": f"Bearer {key}",
        "User-Agent": "hermes-agent/1.0",
        "Accept": "application/json",
    }
    data: Optional[dict] = None
    method = "GET"
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = body
        method = "POST"

    last: tuple[int, str] = (0, "network error")
    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.request(method, url, headers=headers, json=data, timeout=timeout)
            if r.status_code in PERMANENT:
                return r.status_code, r.text[:300]
            if r.status_code == 200:
                return 200, r.text
            last = (r.status_code, r.text[:300])  # transiente
        except requests.RequestException as e:
            last = (0, f"network error: {type(e).__name__}")
        if attempt < MAX_RETRIES:
            time.sleep(BASE_DELAY * (2 ** attempt))
    return last


def probe(base_url: str, key: str, model_id: str) -> bool:
    status, _ = _request(
        f"{base_url}/chat/completions", key,
        body={"model": model_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
    )
    if status not in (200, 429):
        print(f"  [probe] {model_id}: HTTP {status}", file=sys.stderr)
    return status in (200, 429)  # 429 = existe e está no ar, só limitado


def is_free(m: dict) -> bool:
    p = m.get("pricing")
    if not isinstance(p, dict) or p.get("prompt") is None or p.get("completion") is None:
        return False  # sem preço explícito != gratuito
    try:
        return float(p["prompt"]) == 0.0 and float(p["completion"]) == 0.0
    except (TypeError, ValueError):
        return False


def is_agent_text(mid: str) -> bool:
    mid = mid.lower()
    return not any(b in mid for b in SKIP_AGENT)


def collect_nvidia(key: str) -> list[dict]:
    status, body = _request(f"{NVIDIA_BASE_URL}/models", key)
    discovered: list[str] = []
    if status == 200:
        try:
            discovered = [m["id"] for m in json.loads(body).get("data", [])
                          if m.get("id") and is_agent_text(m["id"])]
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    if not discovered:
        print(f"  [nvidia] /models HTTP {status}, usando lista de prioridade", file=sys.stderr)
        discovered = list(NVIDIA_PREFERRED)
    ordered = [m for m in NVIDIA_PREFERRED if m in discovered]
    ordered += [m for m in discovered if m not in NVIDIA_PREFERRED]
    out: list[dict] = []
    for mid in ordered[:NVIDIA_PROBE_CAP]:
        if probe(NVIDIA_BASE_URL, key, mid):
            out.append({"model": mid, "provider": "nvidia",
                        "base_url": NVIDIA_BASE_URL, "context": 0})
    return out


def collect_openrouter(key: str) -> list[dict]:
    """Coleta modelos OpenRouter gratuitos (:free ou pricing 0) e valida por ping real."""
    out: list[dict] = []
    status, body = _request(f"{OPENROUTER_BASE_URL}/models", key)
    if status != 200:
        print(f"  [openrouter] /models HTTP {status}", file=sys.stderr)
        return out
    try:
        models = json.loads(body).get("data", [])
    except json.JSONDecodeError:
        return out
    for m in models:
        mid = m.get("id") or ""
        if not mid or not is_agent_text(mid):
            continue
        if ":free" not in mid and not is_free(m):
            continue
        out.append({
            "model": mid,
            "provider": "openrouter",
            "base_url": OPENROUTER_BASE_URL,
            "context": int(m.get("context_length") or 0),
        })
    out.sort(key=_or_rank)
    return [c for c in out[:OPENROUTER_PROBE_CAP] if probe(c["base_url"], key, c["model"])]


def collect_nous(key: str) -> list[dict]:
    out: list[dict] = []
    status, body = _request(f"{NOUS_BASE_URL}/models", key)
    if status != 200:
        print(f"  [nous] /models HTTP {status}", file=sys.stderr)
        return out
    try:
        models = json.loads(body).get("data", [])
    except json.JSONDecodeError:
        return out
    for m in models:
        if not is_free(m):
            continue
        mid = m["id"]
        if not is_agent_text(mid):
            continue
        out.append({
            "model": mid,
            "provider": "custom",          # provider nativo "nous" exige OAuth
            "base_url": NOUS_BASE_URL,
            "context": int(m.get("context_length") or 0),
            "key_env": "NOUS_API_KEY",
        })
    out.sort(key=lambda c: c["context"], reverse=True)
    # valida os top-N por ping real
    return [c for c in out[:PROBE_CAP] if probe(c["base_url"], key, c["model"])]


def collect_cloudflare(key: str) -> list[dict]:
    """Sonda Workers AI pelo endpoint OpenAI-compatível (/ai/v1/chat/completions),
    o mesmo que o cliente usará em produção."""
    out: list[dict] = []
    for mid in CLOUDFLARE_CANDIDATES:
        if not probe(CLOUDFLARE_BASE_URL, key, mid):
            continue
        out.append({
            "model": mid,
            "provider": "custom",
            "base_url": CLOUDFLARE_BASE_URL,
            "key_env": "CLOUDFLARE_API_TOKEN",
        })
    return out


def build_chain(nvidia: list[dict], openrouter: list[dict], nous: list[dict], cloudflare: list[dict]) -> list[dict]:
    """Cadeia final: nvidia + openrouter + nous + cloudflare (ordem de preferência).

    Os candidatos JÁ foram validados (probe 200) em collect_*. Aqui só se
    monta a cadeia na ordem de preferência, sem re-fazer requisição."""
    chain: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add(cands: list[dict], n: int):
        for c in cands:
            key = (c["provider"], c["model"])
            if key in seen:
                continue
            entry = {
                "provider": c["provider"],
                "model": c["model"],
                "base_url": c["base_url"],
            }
            if c.get("key_env"):
                entry["key_env"] = c["key_env"]
            chain.append(entry)
            seen.add(key)
            if len(chain) >= n:
                return

    add(nvidia, 3)
    add(openrouter, 5)
    add(nous, 7)
    add(cloudflare, TOP_N)
    return chain


def apply_fallback_chain(chain: list[dict], check_only: bool) -> bool:
    import yaml  # PyYAML disponível no venv do Hermes

    txt = CONFIG_YAML.read_text(encoding="utf-8")
    data = yaml.safe_load(txt) or {}

    current = data.get("fallback_providers") or []
    new = chain
    if current == new:
        print("[OK] fallback_providers já atualizado — nada a fazer.")
        return False

    print(f"[update] fallback_providers -> {len(new)} entradas:")
    for e in new:
        print(f"    {e['provider']:11s} / {e['model']}")

    if check_only:
        return False

    backup = CONFIG_YAML.with_suffix(".yaml.pre-update-free-models-" + time.strftime("%Y%m%d-%H%M%S"))
    try:
        backup.write_text(txt, encoding="utf-8")
    except OSError:
        pass

    data["fallback_providers"] = new
    tmp = CONFIG_YAML.with_suffix(".yaml.tmp")
    tmp.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    tmp.replace(CONFIG_YAML)
    return True


def restart_gateway() -> None:
    """Reinicia o gateway DESTE perfil (profiles/<nome> -> hermes-gateway-<nome>.service;
    perfil default -> hermes-gateway.service). --no-block evita TimeoutExpired quando o
    gateway demora a drenar sessões."""
    import subprocess
    unit = (f"hermes-gateway-{HERMES_HOME.name}.service"
            if HERMES_HOME.parent.name == "profiles" else "hermes-gateway.service")
    print(f"[acao] reiniciando {unit}...")
    try:
        r = subprocess.run(["systemctl", "--user", "--no-block", "restart", unit],
                           capture_output=True, text=True, timeout=30)
        print(f"[restart] {unit} rc={r.returncode} {(r.stderr or '').strip()[:200]}")
        if r.returncode != 0:
            print(f"[restart] FALHOU ao enfileirar {unit}", file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"[restart] FALHOU: {e!r}", file=sys.stderr)


def main() -> int:
    check_only = "--check" in sys.argv

    nv_key = load_key("NVIDIA_API_KEY")
    or_key = load_key("OPENROUTER_API_KEY")
    no_key = load_key("NOUS_API_KEY")

    print("=== nvidia ===")
    nvidia = collect_nvidia(nv_key) if nv_key else []
    print(f"  {len(nvidia)} previews respondendo")

    print("=== openrouter ===")
    openrouter = collect_openrouter(or_key) if or_key else []
    print(f"  {len(openrouter)} modelos free respondendo")

    print("=== nous ===")
    nous = collect_nous(no_key) if no_key else []
    print(f"  {len(nous)} candidatos free")

    print("=== cloudflare ===")
    cf_key = load_key("CLOUDFLARE_API_TOKEN")
    cloudflare = collect_cloudflare(cf_key) if cf_key else []
    print(f"  {len(cloudflare)} modelos respondendo")

    chain = build_chain(nvidia, openrouter, nous, cloudflare)
    if not chain:
        print("[ERRO] nenhum candidato validado — nada alterado.", file=sys.stderr)
        return 1

    print(f"\n[cadeia] {len(chain)} fallbacks selecionados")
    changed = apply_fallback_chain(chain, check_only)
    if changed:
        print("[OK] config.yaml atualizado.")
    elif check_only:
        print("[check] sem alterações aplicadas; gateway não reiniciado.")

    if not check_only:
        restart_gateway()

    return 0


if __name__ == "__main__":
    sys.exit(main())
