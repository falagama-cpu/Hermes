#!/usr/bin/env bash
# Fase C4/C5/C9 — runner standalone (sem dependência pytest).
# Cada arquivo é isolado: escreve HERMES_HOME em tempfile.mkdtemp, NÃO toca
# config.yaml/state.db/jobs.json/executions.db de produção.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${PYTHON:-python3}"
pass=0; fail=0
run() { printf '\n=== %s ===\n' "$1"; if "$PY" "$DIR/$1"; then pass=$((pass+1)); else fail=$((fail+1)); echo "!! FAIL: $1"; fi; }
run run_tests.py
# Mesma suíte de infraestrutura contra o seletor V5 unificado + testes da skill V5.
V5="$DIR/../../skills/free-llm-v5"
printf '\n=== run_tests.py [SELECTOR=V5] ===\n'
if SELECTOR="$V5/scripts/hermes-free-model-selector-v5.py" "$PY" "$DIR/run_tests.py"; then pass=$((pass+1)); else fail=$((fail+1)); echo "!! FAIL: run_tests.py [V5]"; fi
printf '\n=== free-llm-v5/tests/test_v5_unified.py ===\n'
if "$PY" "$V5/tests/test_v5_unified.py"; then pass=$((pass+1)); else fail=$((fail+1)); echo "!! FAIL: test_v5_unified.py"; fi
run test_b3_council.py
run test_b2_runtime_retry.py
run test_b_hardening_policy.py
run run_failure_triage_recipe.py
printf '\n=== SUMMARY suites_pass=%d suites_failed=%d ===\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
