"""Health check + pool estendida de modelos livres para o council.

Lê reliability.json/catalog.json do seletor v4, testa cada modelo confiável
e devolve os que responderam de fato. Usado para dar diversidade real ao
council quando o config.yaml tem só 4 slots e alguns estão em rate limit.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from council import HERMES_HOME, _load_profile_env  # noqa: E402

_load_profile_env()

SEL = HERMES_HOME / "model-selector"
POOL_OUT = pathlib.Path(tempfile.gettempdir()) / "council_pool_vivos.json"

URLS = {
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "nous": ("https://inference-api.nousresearch.com/v1", "NOUS_API_KEY"),
    "cloudflare": (
        "https://api.cloudflare.com/client/v4/accounts/"
        + os.environ.get("CLOUDFLARE_ACCOUNT_ID", "<CLOUDFLARE_ACCOUNT_ID>") + "/ai/v1",
        "CLOUDFLARE_API_TOKEN",
    ),
}

# modelos a ignorar no health check (ex.: COUNCIL_POOL_BLOCKLIST="a/b,c/d")
BLOCKLIST = {m.strip() for m in os.environ.get("COUNCIL_POOL_BLOCKLIST", "").split(",") if m.strip()}


def _extrai_content(msg: dict) -> str | None:
    """Modelos de raciocínio podem devolver content=null + reasoning_content."""
    return msg.get("content") or msg.get("reasoning_content")


async def _testa(client: httpx.AsyncClient, source: str, model: str) -> dict:
    url, key_name = URLS[source]
    api_key = os.environ.get(key_name, "")
    rec = {"source": source, "model": model, "base_url": url,
           "api_key_env": key_name, "ok": False, "erro": None, "ms": None}
    if not api_key:
        rec["erro"] = f"sem {key_name}"
        return rec
    t0 = time.time()
    try:
        r = await client.post(
            f"{url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model,
                  "messages": [{"role": "user", "content": "Responda apenas: OK"}],
                  "max_tokens": 64},
            timeout=90,
        )
        if r.status_code != 200:
            rec["erro"] = f"HTTP {r.status_code}"
            return rec
        msg = r.json()["choices"][0]["message"]
        txt = _extrai_content(msg)
        if not txt:
            rec["erro"] = "resposta vazia"
            return rec
        rec["ok"] = True
        rec["ms"] = round((time.time() - t0) * 1000)
        rec["sample"] = txt.strip()[:40]
    except Exception as e:  # noqa: BLE001 — health check, qualquer falha = morto
        rec["erro"] = f"{type(e).__name__}: {str(e)[:50]}"
    return rec


async def main() -> int:
    rel = json.loads((SEL / "reliability.json").read_text(encoding="utf-8"))
    confiaveis = sorted(
        k for k, v in rel.items()
        if v.get("fails", 1) == 0 and v.get("last_ok")
    )

    alvos = []
    for k in confiaveis:
        source, _, model = k.partition("::")
        if source not in URLS or model in BLOCKLIST:
            continue
        alvos.append((source, model))

    print(f"Testando {len(alvos)} modelos confiáveis...\n")
    async with httpx.AsyncClient() as client:
        res = await asyncio.gather(*[_testa(client, s, m) for s, m in alvos])

    vivos = [r for r in res if r["ok"]]
    for r in sorted(res, key=lambda x: (not x["ok"], x["ms"] or 0)):
        marca = "✓" if r["ok"] else "✗"
        info = f"{r['ms']}ms" if r["ok"] else r["erro"]
        print(f"  {marca} {r['source']:12} {r['model']:52} {info}")

    POOL_OUT.write_text(json.dumps(vivos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nPOOL VIVA: {len(vivos)}/{len(alvos)} → {POOL_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
