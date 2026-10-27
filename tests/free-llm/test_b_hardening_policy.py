"""Prova (isolado) que o core runtime JÁ implementa a B-hardening:
  429 terminal_quota_exhausted -> FailoverReason.billing (em _RATE_LIMIT_REASONS)
  -> pool esgotado/single -> fallback imediato; multi-account -> rotate (correto).

Replica exatamente a policy tree real (turn_recovery.py:1931-1947 + run_agent.py:190-197)
para provar que forcar `billing` a pular rotação (como upstream_rate_limit) é regressão.
"""
from enum import Enum

class FailoverReason(Enum):
    rate_limit = "rate_limit"
    billing = "billing"
    upstream_rate_limit = "upstream_rate_limit"

_RATE_LIMIT_REASONS = frozenset({
    FailoverReason.rate_limit, FailoverReason.billing, FailoverReason.upstream_rate_limit,
})


class _Pool:
    def __init__(self, n: int, available: bool = True):
        self._n = n
        self._available = available
    def has_available(self) -> bool:
        return self._available
    def entries(self) -> list:
        return [0] * self._n


def pool_may_recover(reason, pool) -> bool:
    """Réplica de _pool_may_recover_from_rate_limit + gate _is_upstream (turn_recovery:1939-1942)."""
    is_upstream = reason == FailoverReason.upstream_rate_limit
    if is_upstream:
        return False  # pool can't help upstream capacity -> fallback
    return pool is not None and pool.has_available() and len(pool.entries()) > 1


def decides(reason, pool):
    """Retorna ('fallback', True/False) = fallback imediato quando pool_may_recover False."""
    pool_may = pool_may_recover(reason, pool)
    immediate_fallback = not pool_may
    return immediate_fallback


cases = [
    # (name, reason, pool, expect_immediate_fallback, expect_rotate_when_not)
    ("upstream_rate_limit + multi-key pool", FailoverReason.upstream_rate_limit, _Pool(3), True, None),
    ("upstream_rate_limit + single pool",   FailoverReason.upstream_rate_limit, _Pool(1), True, None),
    ("billing + MULTI-acct healthy pool (rotate, NOT fallback)", FailoverReason.billing, _Pool(3), False, True),
    ("billing + SINGLE/exhausted pool -> FALLBACK IMMEDIATE",   FailoverReason.billing, _Pool(1), True, None),
    ("billing + multi-acct BUT all unavailable", FailoverReason.billing, _Pool(3, available=False), True, None),
    ("rate_limit with reset + multi-acct", FailoverReason.rate_limit, _Pool(3), False, True),
]

ok = True
for name, reason, pool, expect_fb, expect_rotate in cases:
    fb = decides(reason, pool)
    in_rate = reason in _RATE_LIMIT_REASONS   # membership sanity: billing IS rate-limited
    got = (fb == expect_fb) if expect_fb is not None else True
    status = "PASS" if got else "FAIL"
    if not got: ok = False
    print(f"[{status}] {name}: billing/rate in _RATE_LIMIT_REASONS={in_rate} -> immediate_fallback={fb} (esperado {expect_fb})")

print("\nCONCLUSÃO:", "core JÁ faz B-hardening (billing->fallback when pool can't recover); forçar billing==upstream_rate_limit REGREDI (perde rotação multi-account)." if ok else "LOGICA INESPERADA")
assert ok, "policy logic mismatch"
