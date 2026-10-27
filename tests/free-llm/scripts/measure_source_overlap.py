#!/usr/bin/env python3
"""Mede a sobreposicao entre um dataset de benchmark e o pool free do seletor.

Usa o mesmo tipo de matching por tokens do aa_scores.py (slug compacto + conjunto de
tokens sem ruido). Conservador: exige >=2 tokens compartilhados e >=50% dos tokens do
nome do pool presentes no nome do dataset.

Exemplos:
  # baixa o parquet e compara com o catalogo do perfil
  python3 measure_source_overlap.py --dataset open-llm-leaderboard/contents \
      --catalog ~/.hermes/profiles/<perfil>/model-selector/catalog.json

  # usa um parquet ja baixado
  python3 measure_source_overlap.py --parquet /tmp/hf.parquet --catalog <...>

Requer pyarrow:
  uv venv .venv --python 3.12 && . .venv/bin/activate && uv pip install pyarrow
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request

NOISE = {
    "instruct", "it", "chat", "fp8", "fast", "bf16", "fp16", "awq", "gptq", "gguf",
    "int4", "int8", "quantized", "hf", "v0", "v1", "free", "preview", "high", "low",
    "max", "xhigh", "reasoning", "base", "distill",
}
VER_RE = re.compile(r"v?\d+(\.\d+)?")


def toks(name: str) -> set:
    """Conjunto de tokens uteis do nome (sem prefixo de criador, sem ruido/versao)."""
    s = name.lower().split("/")[-1]
    s = re.sub(r"[^a-z0-9.]+", " ", s)
    return {t for t in s.split() if t and t not in NOISE and not VER_RE.fullmatch(t)}


def covers(a: set, b: set) -> bool:
    """a cobre b se compartilham >=2 tokens e >=50% dos tokens de a."""
    if len(a) < 2:
        return False
    inter = a & b
    return len(inter) >= 2 and len(inter) / len(a) >= 0.5


def parquet_url(dataset: str) -> str:
    api = f"https://huggingface.co/api/datasets/{dataset}/parquet/default/train"
    with urllib.request.urlopen(api, timeout=60) as r:
        urls = json.load(r)
    return urls[0] if isinstance(urls, list) else urls


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", required=True,
                    help="model-selector/catalog.json do perfil")
    ap.add_argument("--dataset", help="ex.: open-llm-leaderboard/contents (baixa o parquet)")
    ap.add_argument("--parquet", help="parquet ja baixado (alternativa a --dataset)")
    ap.add_argument("--name-column", default="fullname")
    ap.add_argument("--out", help="onde salvar o parquet baixado (default: cwd)")
    ap.add_argument("--max-rows", type=int, default=0)
    args = ap.parse_args()

    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("pyarrow ausente: uv venv .venv --python 3.12 && uv pip install pyarrow",
              file=sys.stderr)
        return 2

    if args.parquet:
        path = args.parquet
    elif args.dataset:
        url = parquet_url(args.dataset)
        base = re.sub(r"[^a-z0-9]+", "_", args.dataset.lower()).strip("_") + ".parquet"
        path = args.out or os.path.join(os.getcwd(), base)
        print(f"baixando {url} -> {path}", file=sys.stderr)
        urllib.request.urlretrieve(url, path)
    else:
        ap.error("informe --dataset ou --parquet")

    table = pq.read_table(path, columns=[args.name_column])
    names = [n for n in table.column(args.name_column).to_pylist() if n]
    if args.max_rows:
        names = names[: args.max_rows]
    ds = [toks(n) for n in names]
    print(f"dataset: {path} | linhas: {len(names)} | coluna: {args.name_column}")

    with open(args.catalog, encoding="utf-8") as fh:
        cat = json.load(fh)
    models = cat["models"] if isinstance(cat, dict) else cat

    total = cov = aa = net = 0
    gain = []
    for m in models:
        t = toks(m["model"])
        aa_name = (m.get("aa") or {}).get("aa_name") or ""
        if aa_name:
            t |= toks(aa_name)
        if not t:
            continue
        total += 1
        has_aa = bool(m.get("aa"))
        if has_aa:
            aa += 1
        if any(covers(t, d) for d in ds):
            cov += 1
            if not has_aa:
                net += 1
                gain.append(m["model"])

    print(f"pool (com tokens uteis): {total}")
    print(f"cobertos pela AA: {aa}")
    print(f"cobertos por esta fonte: {cov}")
    print(f"GANHO LIQUIDO (so esta fonte): {net}")
    for g in gain:
        print("  +", g)
    print(f"sem nenhuma das duas: {total - aa - net}")
    print("nota: matcher conservador -> a cobertura reportada e um piso")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
