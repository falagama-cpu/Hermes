#!/usr/bin/env python3
"""Fase B3 - council: swap-on-429 + provider-dedup de reserves (isolado, sem rede).

Verifica (cópia isolada de skills/council/council.py):
  - pick_reserve nunca devolve um reserva do MESMO provider que falhou
  - load_reserves exclui modelos em quarentena (fails>=3) e not_free
  - _chat: 429 sobe a falha 1x (swap imediato, sem backoff); 500 ainda retria; 200 ok
"""
import asyncio, json, os, sys, importlib.util, tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # fixtures: skills/council/council.py
CHOME = Path(tempfile.mkdtemp(prefix="t_b3_council_"))
(CHOME / "model-selector").mkdir(parents=True, exist_ok=True)
os.environ["HERMES_HOME"] = str(CHOME)
for k in ("NOUS_API_KEY","OPENROUTER_API_KEY","CLOUDFLARE_API_TOKEN"):
    os.environ[k] = "fk"
os.environ["CLOUDFLARE_ACCOUNT_ID"] = "acc"
sys.path.insert(0, str(ROOT / "skills" / "council"))  # resolve imports do council (se houver)

# config.yaml zeroed (sem moa/fallbacks) + stubs de estado
(CHOME / "config.yaml").write_text(
    'model:\n  provider: openrouter\n  default: poolside/lagunaH:free\n  base_url: https://openrouter.ai/api/v1\n'
    'moa:\n  presets: {}\n  default_preset: default\nfallback_providers: []\n'
)
(CHOME / "model-selector" / "reliability.json").write_text(
    json.dumps({"nous::famQ": {"fails": 3, "samples": 3}})
)
(CHOME / "model-selector" / "not_free.json").write_text("{}")
(CHOME / "model-selector" / "catalog.json").write_text(json.dumps({
    "models": [
        {"model": "nous/lagunaQ:free", "source": "nous", "family": "famQ", "aa": {"intel": 80}},
        {"model": "poolside/lagunaH:free", "source": "openrouter", "family": "famH", "aa": {"intel": 85}},
    ]
}))

CSCRIPT = ROOT / "skills" / "council" / "council.py"
spec = importlib.util.spec_from_file_location("council_b3", CSCRIPT)
council = importlib.util.module_from_spec(spec)
sys.modules["council_b3"] = council   # dataclass exige o módulo registrado
spec.loader.exec_module(council)

try:
    import httpx
except Exception:
    httpx = None

results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" :: {extra}"))

M = council.Member
pk = council.provider_key

# ---- A. pick_reserve nunca reescorre o mesmo provider que falhou ----
m_nous = M("n-m", "custom", "nous/x:free", "https://inference-api.nousresearch.com/v1", "fk")
m_or = M("or-m", "openrouter", "openrouter/y:free", "https://openrouter.ai/api/v1", "fk")
failed = {pk(m_nous)}
res = council.pick_reserve([m_nous, m_or], [], failed)
check("A.pick_reserve_avoids_failed_provider",
      res is not None and pk(res) != pk(m_nous),
      f"got={getattr(res,'model',None)} pk={pk(res) if res else None}")
# todos reservas do provider falho -> None
check("A2.pick_reserve_empty_when_all_failed",
      council.pick_reserve([M("n2","custom","nous/x:free","https://inference-api.nousresearch.com/v1","fk")],
                             [], {pk(m_nous)}) is None)

# ---- B. load_reserves exclui quarentena (fails>=3) e not_free ----
reserves = council.load_reserves(exclude_models=set())
models = [r.model for r in reserves]
check("B.load_reserves_excludes_quarantined", "nous/lagunaQ:free" not in models, f"models={models}")
check("B2.load_reserves_keeps_healthy", "poolside/lagunaH:free" in models, f"models={models}")

# ---- C. _chat: 429 swap-on-first, 500 retrai, 200 ok ----
if httpx:
    calls = {"n": 0}
    class R:
        def __init__(self, code, body=None):
            self.status_code = code
            self.request = object()         # exigido por httpx.HTTPStatusError
            self._body = body or {"choices": [{"message": {"content": "hi"}}]}
        def raise_for_status(self):
            if self.status_code >= 400:
                raise httpx.HTTPStatusError(f"{self.status_code}", request=self.request, response=self)
        def json(self):
            return self._body

    class FakeClient:
        def __init__(self, code): self.code = code
        async def post(self, url, json, headers, timeout):
            calls["n"] += 1
            return R(self.code)

    async def _sleep_patch(*a, **k): return None
    council.asyncio.sleep = _sleep_patch  # nada dorme no teste

    async def run_429():
        with __import__("contextlib").suppress(Exception) as cm:
            await council._chat(FakeClient(429), m_or, [{"role":"user","content":"q"}], retries=3)
        return calls["n"]
    n429 = asyncio.run(run_429())
    check("C.429_swaps_immediately_call_count_1", n429 == 1, f"calls={n429}")

    async def run_500():
        calls["n"] = 0
        with __import__("contextlib").suppress(Exception):
            await council._chat(FakeClient(500), m_or, [{"role":"user","content":"q"}], retries=3)
        return calls["n"]
    n500 = asyncio.run(run_500())
    check("C2.500_still_retries", n500 == 3, f"calls={n500}")  # retries=3 -> 3 attempts

    async def run_200():
        calls["n"] = 0
        t = await council._chat(FakeClient(200), m_or, [{"role":"user","content":"q"}], retries=3)
        return t, calls["n"]
    t200, n200 = asyncio.run(run_200())
    check("C3.200_returns_text", t200 == "hi" and n200 == 1, f"text={t200!r} calls={n200}")
else:
    print("SKIP C: httpx indisponível")

ok = all(c for _,c,_ in results)
print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
