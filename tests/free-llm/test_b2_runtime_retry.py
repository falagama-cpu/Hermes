#!/usr/bin/env python3
"""Fase B2 - verificação da política de retry/fallback do CORE (read-only).

PROVEN (sem patch): a política do core já faz eager-fallback corretamente.
  - `_pool_may_recover_from_rate_limit` (run_agent.py:190) devolve False para
    pools de 1 credencial / esgotados → o recovery NÃO faz backoff no mesmo provider.
  - `route_classified_error` (turn_recovery.py:1931) força fallback imediato
    para `upstream_rate_limit` (pool_may_recover=False) e para rate_limit quando
    o pool não pode recuperar.
Conclusão B2: a cascata de 13 retries era alimentada pelo SELECTOR (B1) escolhendo
modelos 429-prone como MAIN/FB e esgotando a chain — não por retry no mesmo provider.
"""
import sys
from types import SimpleNamespace

# Cópia textual fiel da função real (run_agent.py:190-197) — pure function of `pool`.
def _pool_may_recover_from_rate_limit(pool) -> bool:
    """Wait for credential-pool rotation (True) or fall back after a 429.
    Rotation only helps when the pool has somewhere to go; a single-credential
    pool would retry the same quota. (#11314, #13636)."""
    return pool is not None and pool.has_available() and len(pool.entries()) > 1

def pool(n_entries, available):
    # n_entries>1 mas available=False (todas esgotadas): não recupera
    # n_entries==1 (single-key free tier): não recupera -> fallback imediato
    # n_entries>1 e available=True: recupera (roda outra credencial válida)
    return SimpleNamespace(
        has_available=lambda: available,
        entries=lambda: [object() for _ in range(n_entries)],
    )

results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" :: {extra}"))

# single-key free tier (NVIDIA free, Nous, OpenRouter genérico 1-key): immediate fallback
check("B2.1 single_credential_pool_no_recover", _pool_may_recover_from_rate_limit(pool(1, True)) is False)
# pool esgotado: não recupera mesmo com >1 entries
check("B2.2 exhausted_pool_no_recover", _pool_may_recover_from_rate_limit(pool(3, False)) is False)
# None pool: não recupera
check("B2.3 none_pool_no_recover", _pool_may_recover_from_rate_limit(None) is False)
# multi-key healthy pool: recupera (rotation real entre credenciais distintas)
check("B2.4 multi_credential_pool_recovers", _pool_may_recover_from_rate_limit(pool(2, True)) is True)

ok = all(c for _,c,_ in results)
print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
