"""aa_scores.py — ranking por benchmarks da Artificial Analysis (https://artificialanalysis.ai/).

Usado pelo hermes-free-model-selector-v4.py. NÃO decide o que é free: o pool já chega
filtrado (só modelos gratuitos). Aqui só anotamos cada modelo com os índices medidos
pela AA para que o score de cada papel use dados de benchmark em vez de heurística
por palavras-chave.

- API: GET https://artificialanalysis.ai/api/v2/data/llms/models, header x-api-key.
  Chave grátis (1000 req/dia) em ARTIFICIAL_ANALYSIS_API_KEY no .env do perfil.
- Cache em model-selector/aa_cache.json (TTL 24h; se a API falhar usa cache até 30 dias).
- Sem chave/sem dados: annotate() devolve status explicativo e o seletor cai na
  heurística antiga (o cron não quebra).

Atribuição exigida pelos termos da AA: dados de https://artificialanalysis.ai/.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

API_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
CACHE_TTL_S = 24 * 3600
STALE_MAX_S = 30 * 24 * 3600
INDEX_KEYS = {
    "intel": "artificial_analysis_intelligence_index",
    "coding": "artificial_analysis_coding_index",
    "agentic": "artificial_analysis_agentic_index",
}

# Sufixos de rota/quantização/data que não mudam o modelo.
_SUFFIXES = re.compile(
    r"(-(fp8|fp16|bf16|int4|int8|fast|lora|hf|it|instruct|chat|preview|free|"
    r"\d{4}|\d{8}|v\d+(\.\d+)?))+$"
)


_VENDOR_PREFIXES = ("nvidia", "meta", "google", "openai", "alibaba", "mistral",
                    "deepseek", "moonshot", "moonshotai", "zai", "z-ai", "ibm", "microsoft")


_NOISE_TOKENS = {"instruct", "it", "chat", "fp8", "fp16", "bf16", "int4", "int8", "fast",
                 "lora", "hf", "preview", "free", "reasoning", "max", "high", "low", "xhigh"}


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _variants(raw: str) -> List[str]:
    """Chaves de matching de um id/slug/nome (sem provider, sem :free, sem sufixos)."""
    s = raw.lower().strip()
    s = s.split(":", 1)[0]                 # :free
    s = s.replace("@cf/", "")
    s = s.rsplit("/", 1)[-1]               # publisher/model -> model
    s = re.sub(r"\(.*?\)", "", s).strip()  # "gpt-oss-20B (high)" -> "gpt-oss-20b"
    s = re.sub(r"[\s_]+", "-", s)
    out = {_compact(s)}
    stripped = _SUFFIXES.sub("", s)
    if stripped and stripped != s:
        out.add(_compact(stripped))
    # Chave por conjunto de tokens: "llama-3.3-70b-instruct-fp8-fast" ==
    # "Llama 3.3 Instruct 70B" (a AA põe as palavras em outra ordem).
    toks = sorted(t for t in re.split(r"[^a-z0-9]+", s) if t and t not in _NOISE_TOKENS)
    if len(toks) >= 2:
        out.add("tok:" + "|".join(toks))
    return [v for v in out if len(v) >= 4]


def _num(entry: Dict[str, Any], key: str) -> Optional[float]:
    ev = entry.get("evaluations") or {}
    v = ev.get(key, entry.get(key))
    return float(v) if isinstance(v, (int, float)) else None


def _fetch(api_key: str, timeout: int = 30) -> List[Dict[str, Any]]:
    req = urllib.request.Request(API_URL, headers={"x-api-key": api_key,
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode())
    data = body.get("data") if isinstance(body, dict) else body
    if not isinstance(data, list) or not data:
        raise ValueError("resposta da AA sem lista 'data'")
    return data


def load_entries(cache_file: Path, api_key: Optional[str]) -> Tuple[List[Dict[str, Any]], str]:
    """Retorna (entradas, origem). origem: 'api' | 'cache' | 'cache-velho' | motivo de falha."""
    cached = None
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = None
    age = time.time() - cached["fetched_at"] if cached else None

    if cached and age < CACHE_TTL_S:
        return cached["data"], "cache"
    if not api_key:
        if cached and age < STALE_MAX_S:
            return cached["data"], "cache-velho (sem ARTIFICIAL_ANALYSIS_API_KEY)"
        return [], "sem ARTIFICIAL_ANALYSIS_API_KEY no .env"
    try:
        data = _fetch(api_key)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if cached and age < STALE_MAX_S:
            return cached["data"], f"cache-velho (API falhou: {exc})"
        return [], f"API da AA falhou: {exc}"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({"fetched_at": time.time(), "data": data}), encoding="utf-8")
    return data, "api"


def build_index(entries: List[Dict[str, Any]]) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, float]]:
    """Mapa chave->índices e máximos do dataset (para normalizar em 0-100)."""
    maxes = {k: 0.0 for k in INDEX_KEYS}
    rows = []
    for e in entries:
        vals = {k: _num(e, f) for k, f in INDEX_KEYS.items()}
        if vals["intel"] is None:
            continue
        for k, v in vals.items():
            if v is not None:
                maxes[k] = max(maxes[k], v)
        rows.append((e, vals))
    index: Dict[str, Dict[str, Any]] = {}
    for e, vals in rows:
        rec = {"aa_name": e.get("name") or e.get("slug"), **vals}
        keys = set()
        creator = str((e.get("model_creator") or {}).get("slug") or "").lower()
        for raw in (e.get("slug"), e.get("name"), e.get("openrouter_api_id")):
            if raw:
                raw = str(raw)
                keys.update(_variants(raw))
                # "nvidia-nemotron-3-super..." -> também "nemotron-3-super..."
                low = re.sub(r"[\s_]+", "-", raw.lower())
                for pre in filter(None, {creator, *_VENDOR_PREFIXES}):
                    if low.startswith(pre + "-"):
                        keys.update(_variants(low[len(pre) + 1:]))
        for k in keys:
            # Várias entradas (ex.: variantes "high"/"low") -> fica a de maior índice.
            if k not in index or (vals["intel"] or 0) > (index[k]["intel"] or 0):
                index[k] = rec
    return index, maxes


def annotate(models: List[Dict[str, Any]], state_dir: Path) -> Dict[str, Any]:
    """Grava m['_aa'] = {intel, coding, agentic (0-100 normalizados), aa_name} nos modelos
    com benchmark na AA. Retorna status para log/relatório."""
    api_key = os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "").strip() or None
    entries, origin = load_entries(state_dir / "aa_cache.json", api_key)
    if not entries:
        return {"used": False, "origin": origin, "matched": 0, "total": len(models)}

    index, maxes = build_index(entries)
    matched = 0
    for m in models:
        mid = str(m.get("_model_id") or "")
        names = [mid, str(m.get("name") or ""), str(m.get("canonical_slug") or "")]
        rec = next((index[v] for raw in names if raw for v in _variants(raw) if v in index), None)
        if not rec:
            continue
        norm = {}
        for k in INDEX_KEYS:
            # Índice ausente no dataset (ex.: agentic não vem no tier free) ou no
            # modelo -> usa o intelligence index normalizado no lugar.
            if rec[k] is None or not maxes[k]:
                norm[k] = round(100.0 * rec["intel"] / maxes["intel"], 1)
            else:
                norm[k] = round(100.0 * rec[k] / maxes[k], 1)
        m["_aa"] = {**norm, "aa_name": rec["aa_name"]}
        matched += 1
    return {"used": matched > 0, "origin": origin, "matched": matched, "total": len(models),
            "dataset": len(entries)}


def role_score(aa: Dict[str, float], role: str, long_ctx_heuristic: float) -> float:
    """Score 0-100 do papel a partir dos índices AA normalizados."""
    i, c, a = aa["intel"], aa["coding"], aa["agentic"]
    if role == "main":
        return 0.40 * c + 0.35 * a + 0.25 * i
    if role == "coding":
        return 0.60 * c + 0.40 * a
    if role == "moa":
        return 0.60 * i + 0.20 * c + 0.20 * a
    if role == "reasoning":
        return 0.80 * i + 0.20 * c
    if role == "long_context":
        # AA não mede contexto no tier free: mistura com a heurística de contexto.
        return 0.50 * long_ctx_heuristic + 0.50 * i
    return 0.50 * i + 0.25 * c + 0.25 * a
