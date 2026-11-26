#!/usr/bin/env python3
"""Testes offline da V5 unificada (sem rede, sem tocar perfil real).

    python3 tests/test_v5_unified.py          # ou: python3 -m unittest discover tests

Cobre: independência da V4, rebinds V5.1, golden de seleção (paridade com a antiga
"V5 integrated"), invariantes de seleção, leitura/renovação do token ChatGPT/Codex
e o wrapper de cron.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
SELECTOR = SCRIPTS / "hermes-free-model-selector-v5.py"
WRAPPER = SCRIPTS / "run_model_selector_v5.py"
FIXTURE = json.loads((HERE / "fixtures" / "pool_20261126.json").read_text(encoding="utf-8"))
logging.disable(logging.CRITICAL)


def _clean_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not (k.endswith("_BASE_URL") or k.startswith(("HERMES_", "CODEX_", "NOUS_", "OPENROUTER_")))}
    env.update(HERMES_HOME=str(home), CODEX_HOME=str(home / "no-codex-cli"))
    return env


def load_selector(home: Path, src_dir: Path = SCRIPTS, name: str = "sel_v5_test"):
    """Importa o seletor com HERMES_HOME isolado (lido no import)."""
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(_clean_env(home))
    try:
        sys.path.insert(0, str(src_dir))
        spec = importlib.util.spec_from_file_location(name, src_dir / SELECTOR.name)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(src_dir))
        os.environ.clear()
        os.environ.update(saved)
    return mod


def jwt(exp_in: float, plan: str = "free", account: str = "acc-123") -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    claims = {"exp": int(time.time() + exp_in),
              "https://api.openai.com/auth": {"chatgpt_plan_type": plan, "chatgpt_account_id": account}}
    return f"{b64({'alg': 'none'})}.{b64(claims)}.sig"


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="t_v5_unified_"))
        (cls.home / "model-selector").mkdir()
        cls.sel = load_selector(cls.home)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.home, ignore_errors=True)

    def pool(self):
        self.sel._REL = {}
        self.sel._AA_STATUS = dict(FIXTURE["aa_status"])
        return copy.deepcopy(FIXTURE["models"])

    def select(self, models):
        s = self.sel
        main = s.choose_main(models)
        fam, src = {main["_family"]}, {main["_source"]}
        moa = s.choose_moa(models, fam, src)
        fam.add(moa["_family"]); src.add(moa["_source"])
        fbs = s.choose_fallbacks(models, fam, src)
        refs = s.choose_moa_references(models, fam | {f["_family"] for f in fbs}, src, 2,
                                       {s._vendor(main), s._vendor(moa)})
        return main, moa, fbs, refs


class T1Standalone(Base):
    def test_imports_without_v4_file(self):
        """Arquivo único: carrega numa pasta que NÃO tem o seletor V4."""
        d = Path(tempfile.mkdtemp(prefix="t_v5_alone_"))
        try:
            for f in ("hermes-free-model-selector-v5.py", "aa_scores.py", "nvidia_official_free.py"):
                shutil.copy(SCRIPTS / f, d / f)
            self.assertFalse(list(d.glob("*v4*")))
            mod = load_selector(self.home, src_dir=d, name="sel_v5_alone")
            self.assertTrue(callable(mod.main))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_no_v4_reference_in_code(self):
        code = SELECTOR.read_text(encoding="utf-8")
        self.assertNotIn("hermes-free-model-selector-v4", code)
        self.assertNotIn("spec_from_file_location", code)


class T2Rebinds(Base):
    def test_selection_functions_are_v51(self):
        s = self.sel
        self.assertIs(s.score_role, s._v51_score_role)
        self.assertIs(s.choose_main, s._v51_choose_main)
        self.assertIs(s.choose_moa, s._v51_choose_moa)
        self.assertIs(s.choose_fallbacks, s._v51_choose_fallbacks)
        self.assertIsNot(s._v4_score_role, s.score_role)

    def test_not_free_is_momentary(self):
        self.assertEqual(self.sel.NOT_FREE_TTL_S, 0)

    def test_v51_constants_isolated_from_v4(self):
        # A infra V4 continua usando o SOURCE_BONUS dela; a seleção V5.1, o próprio.
        self.assertEqual(self.sel.SOURCE_BONUS["cloudflare"], 3.0)
        self.assertEqual(self.sel._V51_SOURCE_BONUS["cloudflare"], 1.0)


class T3Golden(Base):
    def test_selection_matches_integrated_golden(self):
        main, moa, fbs, refs = self.select(self.pool())
        exp = FIXTURE["expected"]
        self.assertEqual(main["_model_id"], exp["main"])
        self.assertEqual(moa["_model_id"], exp["moa"])
        self.assertEqual([[f["_model_id"], f["_fallback_profile"]] for f in fbs], exp["fallbacks"])
        self.assertEqual([r["_model_id"] for r in refs], exp["refs"])

    def test_score_role_matches_golden(self):
        pool = self.pool()
        for key, want in FIXTURE["expected"]["score_role"].items():
            mid, src, role = key.split("|")
            m = next(x for x in pool if x["_model_id"] == mid and x["_source"] == src)
            self.assertAlmostEqual(self.sel.score_role(m, role), want, places=6, msg=key)

    @unittest.expectedFailure
    def test_order_independent(self):
        """DEFEITO CONHECIDO da seleção V5.1 (auditoria 2026-11-26, achado A1): empate
        exato de _role_score (ex.: 79.69 x 79.69) é decidido pela ORDEM do catálogo, e
        a rota dentro da família ignora a qualidade. 100 embaralhamentos do fixture ->
        2 MAINs e 83 seleções distintas (V4: 1). Quando o algoritmo for corrigido este
        teste passa e o expectedFailure deve sair."""
        import random
        a = self.select(self.pool())
        for seed in range(5):
            p = self.pool(); random.Random(seed).shuffle(p)
            b = self.select(p)
            self.assertEqual(a[0]["_model_id"], b[0]["_model_id"])
            self.assertEqual([f["_model_id"] for f in a[2]], [f["_model_id"] for f in b[2]])


class T4Invariants(Base):
    def test_roles_never_repeat_family(self):
        main, moa, fbs, refs = self.select(self.pool())
        fams = [main["_family"], moa["_family"]] + [f["_family"] for f in fbs] + [r["_family"] for r in refs]
        self.assertEqual(len(fams), len(set(fams)), fams)

    def test_fallback_count_and_profiles(self):
        _, _, fbs, _ = self.select(self.pool())
        self.assertEqual(len(fbs), self.sel.FALLBACK_COUNT)
        self.assertEqual([f["_fallback_profile"] for f in fbs], ["coding-agent", "reasoning", "long-context"])

    def test_empty_and_single_pool(self):
        s = self.sel
        self.assertIsNone(s.choose_main([]))
        one = self.pool()[:1]
        self.assertEqual(s.choose_main(one)["_model_id"], one[0]["_model_id"])
        self.assertIsNone(s.choose_moa(one, {one[0]["_family"]}, {one[0]["_source"]}))
        self.assertEqual(s.choose_fallbacks(one, {one[0]["_family"]}, set()), [])

    def test_input_not_mutated(self):
        p = self.pool(); before = copy.deepcopy(p)
        self.select(p)
        self.assertEqual(p, before)


class T5Codex(Base):
    def setUp(self):
        self.auth = self.home / "auth.json"
        self.cli = self.home / "no-codex-cli"
        self.auth.unlink(missing_ok=True)
        shutil.rmtree(self.cli, ignore_errors=True)
        self.sel._CODEX_STATUS.clear()
        self.sel.CODEX_REFRESH = False
        os.environ["CODEX_HOME"] = str(self.cli)

    def write_auth(self, providers_tok=None, pool_toks=(), bom=False):
        data = {"providers": {}, "credential_pool": {}}
        if providers_tok:
            data["providers"]["openai-codex"] = {"tokens": {"access_token": providers_tok, "refresh_token": "r"}}
        if pool_toks:
            data["credential_pool"]["openai-codex"] = [{"id": f"p{i}", "access_token": t}
                                                       for i, t in enumerate(pool_toks)]
        raw = json.dumps(data)
        self.auth.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw.encode())

    def test_valid_singleton(self):
        self.write_auth(jwt(3600, plan="plus"))
        tok, headers = self.sel._codex_token()
        self.assertEqual(headers["ChatGPT-Account-ID"], "acc-123")
        self.assertEqual(self.sel._CODEX_STATUS["plan"], "plus")
        self.assertEqual(self.sel._CODEX_STATUS["origin"], "auth.json:providers")

    def test_expired_singleton_falls_to_pool(self):
        self.write_auth(jwt(-10), pool_toks=[jwt(-5), jwt(7200)])
        self.assertIsNotNone(self.sel._codex_token())
        self.assertEqual(self.sel._CODEX_STATUS["origin"], "auth.json:pool:p1")

    def test_auth_json_with_bom(self):
        """Windows: auth.json com BOM era descartado pela V4 (json.loads falhava)."""
        self.write_auth(jwt(3600), bom=True)
        self.assertIsNotNone(self.sel._codex_token())

    def test_codex_cli_fallback(self):
        self.cli.mkdir()
        (self.cli / "auth.json").write_text(json.dumps({"tokens": {"access_token": jwt(3600)}}))
        self.assertIsNotNone(self.sel._codex_token())
        self.assertEqual(self.sel._CODEX_STATUS["origin"], "codex-cli")

    def test_expired_without_refresh_reports_reason(self):
        self.write_auth(jwt(100))  # < CODEX_TOKEN_SKEW_S
        self.assertIsNone(self.sel._codex_token())
        self.assertIn("expirado/expirando", self.sel._CODEX_STATUS["reason"])

    def test_missing_and_non_jwt(self):
        self.assertIsNone(self.sel._codex_token())
        self.assertIn("ausente", self.sel._CODEX_STATUS["reason"])
        self.write_auth("not-a-jwt")
        self.assertIsNone(self.sel._codex_token())
        self.assertIn("não é JWT", self.sel._CODEX_STATUS["reason"])

    def test_refresh_delegated_to_hermes_cli(self):
        """Token expirado + refresh ligado: chama `hermes auth refresh openai-codex`
        (fake no PATH, que regrava auth.json como o Hermes faria) e relê."""
        self.write_auth(jwt(-60))
        bindir = self.home / "bin"; bindir.mkdir(exist_ok=True)
        fake = bindir / "hermes"
        log = self.home / "hermes_calls.txt"
        fake.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\n"
            f"open({str(log)!r}, 'a').write(' '.join(sys.argv[1:]) + ' HOME=' + os.environ['HERMES_HOME'] + '\\n')\n"
            f"p = os.path.join(os.environ['HERMES_HOME'], 'auth.json')\n"
            f"json.dump({{'providers': {{'openai-codex': {{'tokens': {{'access_token': {jwt(7200)!r}}}}}}}}}, open(p, 'w'))\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        old_path = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bindir}{os.pathsep}{old_path}"
        self.sel.CODEX_REFRESH = True
        try:
            self.assertIsNotNone(self.sel._codex_token())
        finally:
            os.environ["PATH"] = old_path
        calls = log.read_text()
        self.assertIn("auth refresh openai-codex", calls)
        self.assertIn(f"HOME={self.home}", calls)


class T6Wrapper(unittest.TestCase):
    def run_wrapper(self, selector_body: str):
        home = Path(tempfile.mkdtemp(prefix="t_v5_wrap_"))
        try:
            (home / "model-selector").mkdir()
            (home / "config.yaml").write_text("model:\n  default: old\n  provider: p\n")
            fake = home / "fake_selector.py"
            fake.write_text(selector_body)
            env = _clean_env(home)
            env["FREE_LLM_V5_SELECTOR"] = str(fake)
            p = subprocess.run([sys.executable, str(WRAPPER)], capture_output=True, text=True, env=env, timeout=60)
            return p.returncode, p.stdout, home
        finally:
            pass

    def test_default_points_to_unified_selector(self):
        self.assertIn('HERE / "hermes-free-model-selector-v5.py"', WRAPPER.read_text(encoding="utf-8"))

    def test_success_reports_selection(self):
        body = ("import json, os, pathlib\n"
                "h = pathlib.Path(os.environ['HERMES_HOME'])\n"
                "(h/'config.yaml').write_text('model:\\n  default: m1\\n  provider: nvidia\\n'\n"
                "  'moa:\\n  aggregator: {model: a1, provider: google}\\n'\n"
                "  'fallback_providers:\\n- {model: f1, provider: cloudflare}\\n')\n"
                "(h/'model-selector'/'state.json').write_text(json.dumps({'main': {'model': 'm1', 'provider': 'nvidia'},"
                " 'moa': {'model': 'a1', 'provider': 'google'}, 'fallbacks': [{'model': 'f1', 'provider': 'cloudflare'}]}))\n")
        rc, out, home = self.run_wrapper(body)
        self.assertEqual(rc, 0, out)
        self.assertIn("TROCOU modelos", out)
        self.assertIn("MAIN: m1 (nvidia)", out)
        self.assertIn("FB1: f1 (cloudflare)", out)
        shutil.rmtree(home, ignore_errors=True)

    def test_state_not_updated_is_failure(self):
        rc, out, home = self.run_wrapper("print('nada')\n")
        self.assertEqual(rc, 3, out)
        self.assertIn("state.json não foi atualizado", out)
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
