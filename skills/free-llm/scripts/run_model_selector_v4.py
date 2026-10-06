#!/usr/bin/env python3
"""run_model_selector_v4.py — wrapper de cron para hermes-free-model-selector-v4.py.

Roda o seletor v4 em subprocesso (--cron-mode --force), guarda o log completo em
model-selector/last_run.log e imprime no stdout APENAS um relatório curto:
status, modelos antes -> depois, warm-up e erros. O stdout é o que o cron entrega
(no Telegram, se o job tiver deliver configurado).

Exit != 0 quando o seletor falha ou o config.yaml não reflete a seleção — o cron
então entrega um alerta de falha em vez de "ok" silencioso.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SELECTOR = HERE / "hermes-free-model-selector-v4.py"
HOME = Path(os.environ.get("HERMES_HOME") or HERE.parent)
CONFIG = HOME / "config.yaml"
STATE = HOME / "model-selector" / "state.json"
LOG = HOME / "model-selector" / "last_run.log"
TIMEOUT = 1500


def snapshot() -> dict:
    """Lê model.default, moa.aggregator, moa.reference_models e fallbacks do config.yaml."""
    import yaml

    c = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    m = c.get("model") or {}
    moa = c.get("moa") or {}
    agg = moa.get("aggregator") or {}
    return {
        "main": f"{m.get('default')} ({m.get('provider')})",
        "moa": f"{agg.get('model')} ({agg.get('provider')})",
        "refs": [f"{r.get('model')} ({r.get('provider')})"
                 for r in moa.get("reference_models") or []],
        "fallbacks": [f"{f.get('model')} ({f.get('provider')})"
                      for f in c.get("fallback_providers") or []],
    }


def diff_line(label: str, a, b) -> str:
    return f"{label}: {b}" if a == b else f"{label}: {a} → {b}"


def main() -> int:
    # Windows: console cp1252 quebra com emojis do relatório.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if not SELECTOR.exists():
        print(f"❌ Seletor de modelos FREE: script não encontrado ({SELECTOR})")
        return 1

    before = snapshot()
    state_mtime = STATE.stat().st_mtime if STATE.exists() else 0
    t0 = datetime.now()
    args = sys.argv[1:] or ["--cron-mode", "--force"]  # cron não passa argv
    try:
        proc = subprocess.run(
            [sys.executable, str(SELECTOR), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT,
            env={**os.environ, "HERMES_HOME": str(HOME), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},  # seletor grava no MESMO perfil
        )
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        rc, out = 124, f"TIMEOUT após {TIMEOUT}s\n{e.stdout or ''}{e.stderr or ''}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(out, encoding="utf-8")
    secs = int((datetime.now() - t0).total_seconds())

    after = snapshot()
    errors = [l.split("| ", 2)[-1] for l in out.splitlines()
              if "| ERROR |" in l or "| WARNING |" in l or "Traceback" in l][-6:]
    aa_line = next((l.split("| ", 2)[-1] for l in out.splitlines()
                    if "Artificial Analysis:" in l), None)

    dry = "--dry-run" in args
    warm = []
    if STATE.exists() and STATE.stat().st_mtime > state_mtime:
        st = json.loads(STATE.read_text(encoding="utf-8"))
        for w in st.get("warmup", []):
            mark = "✅" if w.get("ok") else "❌"
            warm.append(f"  {mark} {w['label']}: HTTP {w.get('http_status')} "
                        f"{w.get('latency_ms')}ms")
        if dry:
            # Simulação: config não é gravado; mostra a seleção proposta do state.json.
            fmt = lambda m: f"{(m or {}).get('model')} ({(m or {}).get('provider')})"
            after = {"main": fmt(st.get("main")), "moa": fmt(st.get("moa")),
                     "refs": [fmt(r) for r in st.get("moa_references") or []],
                     "fallbacks": [fmt(f) for f in st.get("fallbacks") or []]}
        exp = (st.get("main") or {}).get("model")
        if not dry and exp and exp not in after["main"]:
            rc = rc or 3
            errors.append(f"config.yaml não reflete a seleção: esperado MAIN={exp}, "
                          f"lido {after['main']}")
    elif rc == 0:
        rc = 3
        errors.append("state.json não foi atualizado — seleção não confirmada")

    changed = before != after
    head = ("❌ FALHOU" if rc else ("🔄 TROCOU modelos" if changed else "✅ OK — sem mudança"))
    if dry and not rc:
        head = "🧪 SIMULAÇÃO (nada gravado)" + (" — trocaria modelos" if changed else "")
    lines = [
        f"🤖 Seletor LLM FREE — {head} ({secs}s)",
        diff_line("MAIN", before["main"], after["main"]),
        diff_line("MOA", before["moa"], after["moa"]),
    ]
    for i in range(max(len(before["refs"]), len(after["refs"]))):
        a = before["refs"][i] if i < len(before["refs"]) else "—"
        b = after["refs"][i] if i < len(after["refs"]) else "—"
        lines.append(diff_line(f"REF{i + 1}", a, b))
    for i in range(max(len(before["fallbacks"]), len(after["fallbacks"]))):
        a = before["fallbacks"][i] if i < len(before["fallbacks"]) else "—"
        b = after["fallbacks"][i] if i < len(after["fallbacks"]) else "—"
        lines.append(diff_line(f"FB{i + 1}", a, b))
    if warm:
        lines += ["Warm-up:", *warm]
    if aa_line:
        lines.append(f"Ranking: {aa_line}")
    if errors:
        lines += ["Avisos/erros:", *[f"  • {e[:200]}" for e in errors]]
    if rc:
        lines.append(f"exit={rc} — log completo: {LOG}")
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
