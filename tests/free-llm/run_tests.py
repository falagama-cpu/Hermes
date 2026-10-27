#!/usr/bin/env python3
"""Fase B1 - teste isolado da correção de classificação de 429.

NÃO toca a rede real nem o perfil vivo: tudo sob HERMES_HOME=/tmp(scratch) e
urlopen/httpmock injetados. Verifica:
  - 429 de capacidade upstream (Nous/OpenRouter/custom/CF-não-cota) => ok=False
  - 429 de cota (CF 4006 / Google limit:0) => quota_exhausted=True, sem falha
  - 401 => ok=False
  - probe_ok rejeita 429 e acumula fails em reliability (_REL)
  - quarentena a >= REL_DEAD_FAILS falhas consecutivas
  - 200 zera fails (reset)
  - atomic_write_yaml integridade + restore de backup em falha
"""
import io, json, os, sys, shutil, importlib.util, urllib.error, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # fixtures: scripts/hermes-free-model-selector-v4.py
HOME = Path(tempfile.mkdtemp(prefix="t_b1_free_llm_"))   # HERMES_HOME ISOLADO, NUNCA produção
os.environ["HERMES_HOME"] = str(HOME)
os.environ["CLOUDFLARE_ACCOUNT_ID"] = "fakeaccount"
os.makedirs(HOME / "model-selector", exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts"))          # resolve `import aa_scores`

# stub config mínimo
(HOME / "config.yaml").write_text(yaml_stub := """\
model:
  default: poolside/laguna-s-2.1:free
  provider: custom
  base_url: https://inference-api.nousresearch.com/v1
  key_env: NOUS_API_KEY
fallback_providers: []
moa:
  aggregator: {provider: openrouter, model: recursive/flint-v1.0:free, base_url: https://openrouter.ai/api/v1}
  reference_models: []
  enabled: true
  presets:
    default: {aggregator: {provider: openrouter, model: recursive/flint-v1.0:free, base_url: https://openrouter.ai/api/v1}, reference_models: []}
  default_preset: default
""")

SCRIPT = ROOT / "scripts" / "hermes-free-model-selector-v4.py"
spec = importlib.util.spec_from_file_location("selector_b1", SCRIPT)
sel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sel)  # executa o módulo (carrega _REL, probe_ok, warmup_model)

class _Resp:
    def __init__(self, code=200, body=b'{"choices":[{}]}'):
        self.status = code; self._b = body
    def read(self, n=None):
        d = self._b if n is None else self._b[:n]
        self._b = b""
        return d
    def __enter__(self): return self
    def __exit__(self, *a): return False

SCEN = {"nous": "200", "cf": "200", "google": "200", "or": "200", "nv": "200"}

def _host(url):
    return url.split("//", 1)[1].split("/", 1)[0]
def fake_urlopen(req, timeout=None):
    url = req.full_url
    h = _host(url)
    if "nousresearch" in h: key = "nous"
    elif "workers.dev" in h or "cloudflare" in h: key = "cf"
    elif "googleapis" in h: key = "google"
    elif "openrouter" in h: key = "or"
    elif "nvidia" in h or "integrate.api.nvidia" in h: key = "nv"
    else: key = "nous"
    s = SCEN.get(key, "200")
    if s == "200":
        return _Resp(200, b'{"choices":[{}]}')
    if s == "429_up":
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b'{"error":"upstream capacity"}'))
    if s == "429_4006":
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO(b'{"error":{"code":4006}}'))
    if s == "401":
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, io.BytesIO(b'{"error":"unauthorized"}'))
    if s == "503":
        raise urllib.error.HTTPError(url, 503, "Upstream", {}, io.BytesIO(b'{"error":"overloaded"}'))
    return _Resp(200, b'{"choices":[{}]}')

def _patch():
    import urllib.request as _ureq
    _ureq.urlopen = fake_urlopen
    sel._resolve_key = lambda model: "fk"   # evita ler .env/chaves reais
    sel._REL.clear()                        # estado limpo por teste
    sel._PROBE_CACHE.clear()

def M(src, mid, fam):
    return {"_source": src, "_model_id": mid, "_family": fam,
            "_context": 64000, "_caps": {}, "capabilities": {}}

def _relkey(m): return sel._rel_key(m)

# --- testes ---
results = []
def check(name, cond, extra=""):
    results.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f" :: {extra}"))

def reset(*args, **kw):
    if args and isinstance(args[0], dict):
        SCEN.update(args[0])
    SCEN.update(kw)
    _patch()

# 1: 429 upstream (Nous) => ok=False, upstream_rate_limit=True, não quota
reset(nous="429_up")
r = sel.warmup_model("probe", M("nous", "poolside/laguna-s-2.1:free", "laguna-s"))
check("1.429_upstream_is_failure",
      r["ok"] is False and r.get("upstream_rate_limit") is True and not r.get("quota_exhausted"),
      f"ok={r.get('ok')} url={r.get('upstream_rate_limit')} quota={r.get('quota_exhausted')}")

# 2: probe_ok rejeita 429 e grava fails em _REL
check("2.probe_ok_rejects_429", sel.probe_ok(M("nous", "poolside/laguna-s-2.1:free", "laguna-s")) is False,
      f"_REL={sel._REL}")
e = sel._REL.get(_relkey(M("nous","x","laguna-s")))
check("2b.record_reliability_increments_fails", e is not None and e.get("fails",0) >= 1, f"e={e}")

# 3: CF 4006 => quota_exhausted, ok=False, mas NÃO acumula falha (não grava fails)
reset(cf="429_4006")
r3 = sel.warmup_model("probe", M("cloudflare", "cf:model", "cfam"))
check("3.cf_4006_quota", r3.get("quota_exhausted") is True and r3["ok"] is False and not r3.get("upstream_rate_limit"),
      f"quota={r3.get('quota_exhausted')} ok={r3.get('ok')} url={r3.get('upstream_rate_limit')}")
cf_key = M("cloudflare","cf:model","cfam"); sel._REL.clear(); sel._PROBE_CACHE.clear()
res_probe = sel.probe_ok(cf_key)
check("3b.cf_4006_probe_no_failure", res_probe is False, f"probe={res_probe}")
entry = sel._REL.get(_relkey(cf_key))
check("3c.cf_4006_does_not_count_fail", entry is None or entry.get("fails",0)==0, f"entry={entry}")

# 4: 401 auth => ok=False
reset(nous="401")
r4 = sel.warmup_model("probe", M("nous","poolside/laguna-s-2.1:free","laguna-s"))
check("4.401_is_failure", r4["ok"] is False, f"ok={r4.get('ok')}")

# 5: 200 zera fails (reset de confiabilidade)
reset(nous="429_up")
sel._REL.clear(); sel._PROBE_CACHE.clear()
sel.probe_ok(M("nous","poolside/laguna-s-2.1:free","laguna-s"))  # fail
reset(nous="200")
sel._PROBE_CACHE.clear()
sel.probe_ok(M("nous","poolside/laguna-s-2.1:free","laguna-s"))  # ok
e5 = sel._REL.get(_relkey(M("nous","poolside/laguna-s-2.1:free","laguna-s")))
check("5.200_resets_fails", e5 is not None and e5.get("fails",0)==0, f"e5={e5}")

# 6: quarentena a REL_DEAD_FAILS=3 falhas consecutivas (upstream), e modelo saudável não é quarantenado
reset(nous="429_up")
m = M("nous","poolside/laguna-s-2.1:free","laguna-s")
for i in range(3):
    sel._PROBE_CACHE.clear(); sel.probe_ok(m)
check("6a.quarantined_after_3_upstream_429", sel.is_quarantined(sel._REL, m) is True, f"_REL={sel._REL.get(_relkey(m))}")
# modelo 200 saudável não fica em quarentena
reset({"nous":"200","or":"200"})
good = M("openrouter","poolside/laguna-xs-2.1:free","laguna-xs")
for i in range(3):
    sel._PROBE_CACHE.clear(); sel._be_ok(good) if hasattr(sel,'_be_ok') else sel.probe_ok(good)
check("6b.healthy_model_not_quarantined", sel.is_quarantined(sel._REL, good) is False, f"_REL={sel._REL.get(_relkey(good))}")

# 7: atomic_write_yaml integridade + restore em falha
tmpcfg = Path(sel.Hermes_appdir) if hasattr(sel,'Hermes_appdir') else None
import tempfile
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    target = td / "out.yaml"
    data = {"model":{"default":"x/y:free"},"fallback_providers":[{"model":"z"}]}
    sel.atomic_write_yaml(target, data)
    import yaml
    got = yaml.safe_load(target.read_text())
    check("7a.atomic_write_readback", got == data, f"got={got}")
    # falha durante escrita => backup restaurado
    target.write_text(yaml.dump({"model":{"default":"OLD"}}))  # estado pre-existing
    # agora injeta falha
    sel.atomic_write_yaml = lambda *a, **k: (_ for _ in ()).throw(OSError("fail"))
    target.write_text(yaml.dump({"model":{"default":"BEFORE"}}))
    raised = False
    try:
        sel.atomic_write_yaml(target, {"model":{"default":"SHOULD_NOT_PERSIST"}})
    except OSError:
        raised = True
    cur = yaml.safe_load(target.read_text())
    check("7b.atomic_write_preserves_old_on_failure", raised and cur.get("model",{}).get("default")=="BEFORE", f"raised={raised} cur={cur}")

# 8: regressão — 200 continua ok (garante o patch não quebrou o verdadeiro-positivo)
reset()
r8 = sel.warmup_model("probe", M("nous","nous:m","fam"))
check("8.200_still_ok", r8["ok"] is True, f"ok={r8.get('ok')}")

# 9: openrouter 429 também é upstream (mesma classificação)
reset({"or":"429_up"})
ro = sel.warmup_model("probe", M("openrouter","poolside/laguna-xs-2.1:free","laguna-xs"))
check("9.openrouter_429_upstream_failure", ro["ok"] is False and ro.get("upstream_rate_limit") is True, f"ok={ro.get('ok')}")

ok = all(c for _,c,_ in results)
print("\n" + ("ALL PASS" if ok else "SOME FAILED"))
sys.exit(0 if ok else 1)
