#!/usr/bin/env python3
"""
update_free_models.py — mantém a lista de modelos FREE de 2 provedores
(NVIDIA, Nous) atualizada no config.yaml do perfil pesquisa.

PROBLEMA que resolve: os provedores trocam os modelos gratuitos com frequência.
Este script cobre os 2 provedores restantes (OpenRouter removido do cron).

As fontes e como cada uma informa "gratuito":
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
  - Se nada mudou, não reescreve o arquivo.

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

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes/profiles/pesquisa"))
ENV_PATH = HERMES_HOME / ".env"
CONFIG_YAML = HERMES_HOME / "config.yaml"

TOP_N = 4                 # quantos fallbacks compõem a cadeia final
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
]

# NVIDIA NIM: candidatos a sondar (previews free). Não há campo de preço no /models.
NVIDIA_CANDIDATES = [
    "minimaxai/minimax-m3",
    "deepseek-ai/deepseek-v4-flash-0731",
    "deepseek-ai/deepseek-v4-pro-0813",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-nano-3-30b-a3b",
    "moonshotai/kimi-k2.6",
]

# nós free: rotear como custom (provider nativo "nous" exige OAuth)
NOUS_BASE_URL = "https://inference-api.nousresearch.com/v1"


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

    for attempt in range(MAX_RETRIES + 1):
        try:
            r = requests.request(method, url, headers=headers, json=data, timeout=timeout)
            if r.status_code in PERMANENT:
                return r.status_code, r.text[:300]
            if r.status_code == 200:
                return 200, r.text
            # transiente -> cai no retry
        except requests.RequestException as e:
            pass
        if attempt < MAX_RETRIES:
            time.sleep(BASE_DELAY * (2 ** attempt))
    return 0, "network error"


def probe(base_url: str, key: str, model_id: str) -> bool:
    status, _ = _request(
        f"{base_url}/chat/completions", key,
        body={"model": model_id, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 8},
    )
    return status == 200


def is_free(m: dict) -> bool:
    p = m.get("pricing", {})
    if not isinstance(p, dict):
        return False
    try:
        return float(p.get("prompt") or 0) == 0.0 and float(p.get("completion") or 0) == 0.0
    except (TypeError, ValueError):
        return False


def is_agent_text(mid: str) -> bool:
    mid = mid.lower()
    return not any(b in mid for b in SKIP_AGENT)


def collect_nvidia(key: str) -> list[dict]:
    out: list[dict] = []
    for mid in NVIDIA_CANDIDATES:
        if not probe("https://integrate.api.nvidia.com/v1", key, mid):
            continue
        out.append({
            "model": mid,
            "provider": "nvidia",
            "base_url": "https://integrate.api.nvidia.com/v1",
            "context": 0,  # NIM não expõe; fica sem ranking por contexto
        })
    return out


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


def build_chain(nvidia: list[dict], nous: list[dict]) -> list[dict]:
    """Cadeia final: nvidia + nous (ordem de preferência).

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
            if len([e for e in chain if e]) >= n:
                return

    add(nvidia, 2)
    add(nous, 4)
    return chain


def apply_fallback_chain(chain: list[dict], check_only: bool) -> bool:
    import yaml  # PyYAML disponível no venv do Hermes

    txt = CONFIG_YAML.read_text(encoding="utf-8")
    data = yaml.safe_load(txt)

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

    backup = CONFIG_YAML.with_suffix(".yaml.pre-update-free-models")
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


def main() -> int:
    check_only = "--check" in sys.argv

    nv_key = load_key("NVIDIA_API_KEY")
    no_key = load_key("NOUS_API_KEY")

    print("=== nvidia ===")
    nvidia = collect_nvidia(nv_key) if nv_key else []
    print(f"  {len(nvidia)} previews respondendo")

    print("=== nous ===")
    nous = collect_nous(no_key) if no_key else []
    print(f"  {len(nous)} candidatos free")

    chain = build_chain(nvidia, nous)
    if not chain:
        print("[ERRO] nenhum candidato validado — nada alterado.", file=sys.stderr)
        return 1

    print(f"\n[cadeia] {len(chain)} fallbacks selecionados")
    changed = apply_fallback_chain(chain, check_only)
    if changed:
        print("[OK] config.yaml atualizado.")
    # Reinicia o gateway para que as novas seleções de modelo entrem em efeito
    print("[acao] reiniciando gateway para assumir novos modelos...")
    import subprocess
    subprocess.run(["hermes", "gateway", "restart"], capture_output=True, timeout=30)


    return 0


if __name__ == "__main__":
    sys.exit(main())
