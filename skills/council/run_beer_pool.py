"""Roda o council sobre o prompt do exemplo usando a pool verificada.

Pool = /tmp/pool_vivos.json (gerada por pool.py: só modelos que responderam).
Diversidade real: 4 modelos de 3 provedores diferentes.
"""
import asyncio
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import council  # noqa: E402

PROMPT = pathlib.Path("/tmp/prompt.txt")
POOL = "/tmp/pool_vivos.json"
OUT_MD = pathlib.Path("/tmp/council_run_v3.md")
OUT_JSON = pathlib.Path("/tmp/council_run_v3.json")


def main() -> int:
    q = PROMPT.read_text()
    t0 = time.time()
    r = asyncio.run(council.run_council(q, top_n=4, pool_path=POOL))
    OUT_MD.write_text(council.to_markdown(r))
    OUT_JSON.write_text(council.to_json(r))

    print(f"TERMINOU em {time.time() - t0:.0f}s")
    print(f"chairman: {r.chairman_model}")
    print(f"final: {len(r.final or '')} chars")
    for s in r.stage1:
        estado = f"ERRO {s.error[:60]}" if s.error else f"{len(s.text)} chars / {s.elapsed_s:.0f}s"
        print(f"  stage1 {s.member:28} {s.model:40} {estado}")
    for s in r.stage2:
        estado = "ERRO" if s.error else str(s.ranking)
        print(f"  stage2 {s.reviewer:28} {estado}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
