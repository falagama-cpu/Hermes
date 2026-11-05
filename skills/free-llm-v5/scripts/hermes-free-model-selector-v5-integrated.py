#!/usr/bin/env python3
"""V5 integrated selector.

Reuses the proven V4 operational infrastructure while replacing only the
model-selection algorithms with the V5.1 role-based implementation.

V4 remains untouched; this file lives only in the isolated V5 directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
V4_PATH = BASE_DIR / "hermes-free-model-selector-v4.py"
V5_PATH = BASE_DIR / "hermes-free-model-selector-v5.1.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


v4 = _load_module("hermes_free_model_selector_v4", V4_PATH)
v5 = _load_module("hermes_free_model_selector_v5_1", V5_PATH)

# V4 keeps a 7-day not-free exclusion. For V5, exclusions must be momentary:
# providers return a fresh catalog every run, so expired exclusions should not
# persist beyond the current execution.
v4.NOT_FREE_TTL_S = 0

# V5.1 algorithms replace V4 selection, while V4 keeps fetch, auth, warm-up,
# validation, persistence, backup, history and reliability.
v4.score_role = v5._score_role_v5_enhanced
v4.choose_main = v5.choose_main_v5
v4.choose_moa = v5.choose_moa
v4.choose_fallbacks = v5.choose_fallbacks


def main() -> int:
    print("Hermes Free LLM Selector V5 integrated: V4 infrastructure + V5.1 selection")
    return v4.main()


if __name__ == "__main__":
    raise SystemExit(main())
