#!/usr/bin/env python3
"""Wrapper de cron para a V5 integrada.

Roda o seletor V5 em subprocesso, guarda o log completo e imprime no stdout
apenas o horário da execução e as seleções MAIN, MOA e fallbacks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Seletor: mesmo diretório deste wrapper; sobrescreva com FREE_LLM_V5_SELECTOR.
SELECTOR = Path(os.environ.get("FREE_LLM_V5_SELECTOR")
                or HERE / "hermes-free-model-selector-v5-integrated.py")
# Perfil alvo: defina HERMES_HOME explicitamente (ex.: ~/.hermes/profiles/<perfil>).
HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
CONFIG = HOME / "config.yaml"
STATE = HOME / "model-selector" / "state.json"
LOG = HOME / "model-selector" / "last_run_v5.log"
STARTED = time.time()
TIMEOUT = 1500


def snapshot() -> dict:
    import yaml

    c = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    m = c.get("model") or {}
    moa = c.get("moa") or {}
    agg = moa.get("aggregator") or {}
    return {
        "main": f"{m.get('default')} ({m.get('provider')})",
        "moa": f"{agg.get('model')} ({agg.get('provider')})",
        "fallbacks": [f"{f.get('model')} ({f.get('provider')})"
                      for f in c.get("fallback_providers") or []],
    }


def fmt_model(item) -> str:
    if not isinstance(item, dict):
        return "—"
    return f"{item.get('model')} ({item.get('provider')})"


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    if not SELECTOR.exists():
        print(f"V5 LLM FREE — ERRO: script não encontrado ({SELECTOR})")
        return 1

    before = snapshot()
    state_mtime = STATE.stat().st_mtime if STATE.exists() else 0
    t0 = datetime.now()
    args = sys.argv[1:] or ["--cron-mode", "--force"]
    try:
        proc = subprocess.run(
            [sys.executable, str(SELECTOR), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT,
            env={**os.environ, "HERMES_HOME": str(HOME), "PYTHONUTF8": "1",
                 "PYTHONIOENCODING": "utf-8"},
        )
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as e:
        rc, out = 124, f"TIMEOUT após {TIMEOUT}s\n{e.stdout or ''}{e.stderr or ''}"

    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.write_text(out, encoding="utf-8")
    secs = int((datetime.now() - t0).total_seconds())

    after = before
    state = None
    if STATE.exists() and STATE.stat().st_mtime > state_mtime:
        try:
            state = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None

    if state:
        after = {
            "main": fmt_model(state.get("main")),
            "moa": fmt_model(state.get("moa")),
            "fallbacks": [fmt_model(f) for f in state.get("fallbacks") or []],
        }

    errors = [line.split("| ", 2)[-1] for line in out.splitlines()
              if "| ERROR |" in line or "| WARNING |" in line or "Traceback" in line][-4:]

    if rc == 0 and state is None:
        rc = 3
        errors.append("state.json não foi atualizado — seleção não confirmada")

    if rc == 0:
        exp = (state or {}).get("main", {}).get("model")
        if exp and exp not in after["main"]:
            rc = 3
            errors.append(f"config.yaml não reflete a seleção: esperado MAIN={exp}, lido {after['main']}")

    head = "FALHOU" if rc else ("TROCOU modelos" if before != after else "OK — sem mudança")
    lines = [
        f"V5 LLM FREE — {head} ({secs}s) — {t0.strftime('%Y-%m-%d %H:%M:%S')}",
        f"MAIN: {after['main']}",
        f"MOA: {after['moa']}",
    ]
    for i, fb in enumerate(after["fallbacks"], 1):
        lines.append(f"FB{i}: {fb}")
    if errors:
        lines += ["Avisos/erros:", *[f"  • {e[:200]}" for e in errors]]
    if rc:
        lines.append(f"exit={rc} — log completo: {LOG}")
    print("\n".join(lines))
    return rc


if __name__ == "__main__":
    sys.exit(main())
