"""Health check + pool estendida de modelos livres para o council.

Lê reliability.json/catalog.json do seletor V5, testa cada modelo confiável
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
from council import HERMES_HOME, _load_profile_env, _chat_base_url  # noqa: E402

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
    # Gemini fala /chat/completions em <base>/openai; _chat_base_url aplica a
    # adaptação (mesma regra do council.py) — não duplicar /openai aqui.
    "google": ("https://generativelanguage.googleapis.com/v1beta", "GOOGLE_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta", "GOOGLE_API_KEY"),
}

# modelos a ignorar no health check (ex.: COUNCIL_POOL_BLOCKLIST="a/b,c/d")
BLOCKLIST = {m.strip() for m in os.environ.get("COUNCIL_POOL_BLOCKLIST", "").split(",") if m.strip()}


def _classe_http(status: int) -> str:
    """Classifica o erro HTTP: autenticação, não encontrado, rate limit, servidor."""
    if status in (401, 403):
        return "autenticacao"
    if status == 404:
        return "nao_encontrado"
    if status == 429:
        return "rate_limit"
    if status >= 500:
        return "servidor"
    return "http"


def _extrai_content(msg: dict) -> str | None:
    """Modelos de raciocínio podem devolver content=null + reasoning_content."""
    return msg.get("content") or msg.get("reasoning_content")


async def _testa(client: httpx.AsyncClient, source: str, model: str) -> dict:
    base, key_name = URLS[source]
    base = _chat_base_url(source, base)
    api_key = os.environ.get(key_name, "")
    rec = {"source": source, "model": model, "base_url": base,
           "api_key_env": key_name, "ok": False, "erro": None,
           "classe": None, "ms": None}
    if not api_key:
        rec["erro"] = f"sem {key_name}"
        rec["classe"] = "sem_credencial"
        return rec
    t0 = time.time()
    try:
        r = await client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model,
                  "messages": [{"role": "user", "content": "responda apenas: OK"}],
                  "max_tokens": 64},
            timeout=90,
        )
    except httpx.TimeoutException:
        rec["erro"] = "timeout"
        rec["classe"] = "timeout"
        return rec
    except httpx.TransportError as e:
        rec["erro"] = f"transporte: {type(e).__name__}"
        rec["classe"] = "transporte"
        return rec
    except Exception as e:  # noqa: BLE001 — health check, qualquer falha = morto
        rec["erro"] = f"{type(e).__name__}: {str(e)[:50]}"
        rec["classe"] = "excecao"
        return rec

    if r.status_code != 200:
        rec["erro"] = f"HTTP {r.status_code}"
        rec["classe"] = _classe_http(r.status_code)
        return rec
    try:
        msg = r.json()["choices"][0]["message"]
    except (ValueError, KeyError, IndexError, TypeError):
        rec["erro"] = "resposta inválida (sem choices[0].message)"
        rec["classe"] = "resposta_invalida"
        return rec
    txt = _extrai_content(msg)
    if not txt:
        rec["erro"] = "resposta vazia"
        rec["classe"] = "resposta_vazia"
        return rec
    rec["ok"] = True
    rec["ms"] = round((time.time() - t0) * 1000)
    rec["sample"] = txt.strip()[:40]
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
        info = f"{r['ms']}ms" if r["ok"] else f"{r['erro']} [{r.get('classe')}]"
        print(f"  {marca} {r['source']:12} {r['model']:52} {info}")

    POOL_OUT.write_text(json.dumps(vivos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nPOOL VIVA: {len(vivos)}/{len(alvos)} → {POOL_OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
