"""Testes isolados da skill Council — implementação V5.1.

Sem chamadas externas: todo HTTP é simulado com respostas controladas.
Cobrem: seleção V5 (A), deduplicação (B), reservas (C), not_free TTL (D),
health check Google/Gemini (E), chairman/swaps (F) e regressão (G).

Uso:
    cd <skill_dir> && .venv/bin/python -m unittest tests.test_v51 -v
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR))

# ---------------------------------------------------------------- helpers ---
class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _ok(content="OK", usage=None):
    payload = {"choices": [{"message": {"content": content}}]}
    if usage is not None:
        payload["usage"] = usage
    return FakeResponse(200, payload)


def _reload_council(home: Path):
    """Recarrega council com HERMES_HOME apontando para um diretório temporário."""
    os.environ["HERMES_HOME"] = str(home)
    import council
    importlib.reload(council)
    return council


def _write_config(home: Path, cfg: dict) -> None:
    import yaml
    (home / "config.yaml").write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")


def _write_json(home: Path, name: str, data) -> None:
    d = home / "model-selector"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(data), encoding="utf-8")


BASE_CONFIG = {
    "model": {
        "default": "nvidia/main-x:free",
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
    },
    "moa": {
        "default_preset": "default",
        "presets": {"default": {
            "aggregator": {"provider": "nvidia", "model": "nvidia/main-x",
                           "base_url": "https://integrate.api.nvidia.com/v1"},
            "reference_models": [
                {"provider": "custom", "model": "meituan/ref:free",
                 "base_url": "https://inference-api.nousresearch.com/v1",
                 "key_env": "NOUS_API_KEY", "enabled": True},
            ],
        }},
        "aggregator": {"provider": "nvidia", "model": "nvidia/main-x",
                       "base_url": "https://integrate.api.nvidia.com/v1"},
        "reference_models": [
            {"provider": "custom", "model": "meituan/ref:free",
             "base_url": "https://inference-api.nousresearch.com/v1",
             "key_env": "NOUS_API_KEY"},
        ],
    },
    "fallback_providers": [
        {"provider": "gemini", "model": "gemini-2.5-flash",
         "base_url": "https://generativelanguage.googleapis.com/v1beta",
         "key_env": "GOOGLE_API_KEY"},
        {"provider": "nvidia", "model": "nvidia/fallback",
         "base_url": "https://integrate.api.nvidia.com/v1"},
    ],
}


class CouncilV51Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.council = _reload_council(self.home)
        for k, v in (("OPENROUTER_API_KEY", "or-key"), ("NVIDIA_API_KEY", "nv-key"),
                     ("NOUS_API_KEY", "nous-key"), ("GOOGLE_API_KEY", "g-key"),
                     ("CLOUDFLARE_API_TOKEN", "cf-token"),
                     ("CLOUDFLARE_ACCOUNT_ID", "acct")):
            os.environ.setdefault(k, v)

    def tearDown(self):
        self._tmp.cleanup()

    # ------------------------------------------------------------ Teste A ---
    def test_a_selecao_v5_ordem(self):
        """Chairman = moa.aggregator; MAIN deduplicado; refs e fallbacks na ordem."""
        _write_config(self.home, BASE_CONFIG)
        members, chairman, deduped = self.council.load_council_members(4, return_log=True)
        self.assertEqual(chairman.model, "nvidia/main-x")
        self.assertEqual(chairman.provider, "nvidia")
        models = [m.model for m in members]
        self.assertEqual(models[0], "nvidia/main-x")
        self.assertIn("meituan/ref:free", models)
        self.assertIn("gemini-2.5-flash", models)
        self.assertIn("nvidia/fallback", models)
        # MAIN (nvidia/main-x:free) é deduplicado por causa do chairman
        self.assertNotIn("nvidia/main-x:free", models)
        self.assertTrue(any(d["model"] == "nvidia/main-x:free" for d in deduped))

    def test_a2_sem_moa_main_vira_chairman(self):
        cfg = json.loads(json.dumps(BASE_CONFIG))
        cfg.pop("moa")
        _write_config(self.home, cfg)
        members, chairman, _ = self.council.load_council_members(4, return_log=True)
        self.assertEqual(chairman.model, "nvidia/main-x:free")
        self.assertEqual(chairman.provider, "openrouter")

    # ------------------------------------------------------------ Teste B ---
    def test_b_dedupe_free(self):
        """Mesmo modelo com e sem :free não repete."""
        cfg = json.loads(json.dumps(BASE_CONFIG))
        cfg["moa"]["presets"]["default"]["aggregator"]["model"] = "nvidia/dup"
        cfg["model"]["default"] = "nvidia/dup:free"
        _write_config(self.home, cfg)
        members, _, deduped = self.council.load_council_members(4, return_log=True)
        self.assertEqual([m.model for m in members].count("nvidia/dup"), 1)
        self.assertTrue(any(d["model"] == "nvidia/dup:free" for d in deduped))

    def test_b_mesmo_modelo_providers_distintos(self):
        """Mesmo modelo em providers diferentes continua deduplicado (por modelo)."""
        cfg = json.loads(json.dumps(BASE_CONFIG))
        cfg["moa"]["presets"]["default"]["aggregator"]["model"] = "shared/m1"
        cfg["fallback_providers"][0]["model"] = "shared/m1"
        _write_config(self.home, cfg)
        members, _, deduped = self.council.load_council_members(4, return_log=True)
        self.assertEqual([m.model for m in members].count("shared/m1"), 1)

    def test_b_mesmo_provider_modelos_distintos(self):
        """Dois modelos diferentes no mesmo provider: só o primeiro na 1ª passada."""
        cfg = json.loads(json.dumps(BASE_CONFIG))
        cfg["moa"]["presets"]["default"]["reference_models"] = [
            {"provider": "nvidia", "model": "nvidia/a",
             "base_url": "https://integrate.api.nvidia.com/v1"},
            {"provider": "nvidia", "model": "nvidia/b",
             "base_url": "https://integrate.api.nvidia.com/v1"},
        ]
        _write_config(self.home, cfg)
        # top_n=2: 1ª passada aceita só um modelo por provider (aggregator, não nvidia/a)
        members2, _, _ = self.council.load_council_members(2, return_log=True)
        nvidia2 = [m.model for m in members2 if m.provider == "nvidia"]
        self.assertEqual(nvidia2, ["nvidia/main-x"])
        self.assertNotIn("nvidia/a", nvidia2)
        # top_n=4: 2ª passada completa com provider repetido
        members4, _, _ = self.council.load_council_members(4, return_log=True)
        nvidia4 = [m.model for m in members4 if m.provider == "nvidia"]
        self.assertEqual(nvidia4.count("nvidia/a"), 1)
        self.assertEqual(nvidia4.count("nvidia/b"), 1)

    def test_b_custom_hosts_distintos(self):
        """custom com hosts diferentes (Nous vs Cloudflare) conta como providers distintos."""
        cfg = json.loads(json.dumps(BASE_CONFIG))
        cfg["moa"]["presets"]["default"]["reference_models"] = [
            {"provider": "custom", "model": "meituan/ref:free",
             "base_url": "https://inference-api.nousresearch.com/v1",
             "key_env": "NOUS_API_KEY"},
            {"provider": "custom", "model": "@cf/openai/gpt-oss-120b",
             "base_url": "https://api.cloudflare.com/client/v4/accounts/acct/ai/v1",
             "key_env": "CLOUDFLARE_API_TOKEN"},
        ]
        _write_config(self.home, cfg)
        members, _, _ = self.council.load_council_members(4, return_log=True)
        customs = [m.model for m in members if m.provider == "custom"]
        self.assertIn("meituan/ref:free", customs)
        self.assertIn("@cf/openai/gpt-oss-120b", customs)

    # ------------------------------------------------------------ Teste C ---
    def test_c_reservas(self):
        """Reservas respeitam catálogo, quarentena, not_free e falhas recentes."""
        _write_config(self.home, BASE_CONFIG)
        now = time.time()
        _write_json(self.home, "catalog.json", {"models": [
            {"source": "nvidia", "model": "nvidia/good", "family": "nvidia/good",
             "capabilities": {"reasoning": 90}},
            {"source": "nvidia", "model": "nvidia/quarantined", "family": "nvidia/quarantined",
             "capabilities": {"reasoning": 95}},
            {"source": "nvidia", "model": "nvidia/notfree", "family": "nvidia/notfree",
             "capabilities": {"reasoning": 99}},
            {"source": "nvidia", "model": "nvidia/flaky", "family": "nvidia/flaky",
             "capabilities": {"reasoning": 80}},
        ]})
        _write_json(self.home, "reliability.json", {
            "nvidia::nvidia/quarantined": {"fails": 5, "last_ok": None, "samples": 5},
            "nvidia::nvidia/flaky": {"fails": 1, "last_ok": "2026-01-01T00:00:00+00:00", "samples": 2},
        })
        _write_json(self.home, "not_free.json", {
            "nvidia/notfree": {"reason": "fora do plano", "ts": now},
        })
        reserves = self.council.load_reserves(set(), limit=12)
        models = [r.model for r in reserves]
        self.assertIn("nvidia/good", models)
        self.assertNotIn("nvidia/quarantined", models)   # quarentena
        self.assertNotIn("nvidia/notfree", models)       # not_free vigente
        self.assertIn("nvidia/flaky", models)            # falha recente não exclui
        # falha recente vai para o fim da fila
        self.assertLess(models.index("nvidia/good"), models.index("nvidia/flaky"))

    # ------------------------------------------------------------ Teste D ---
    def test_d_not_free_ttl(self):
        """Entradas antigas não bloqueiam; entradas recentes bloqueiam."""
        _write_config(self.home, BASE_CONFIG)
        now = time.time()
        _write_json(self.home, "catalog.json", {"models": [
            {"source": "nvidia", "model": "nvidia/old", "family": "nvidia/old",
             "capabilities": {"reasoning": 90}},
            {"source": "nvidia", "model": "nvidia/new", "family": "nvidia/new",
             "capabilities": {"reasoning": 90}},
        ]})
        _write_json(self.home, "reliability.json", {})
        _write_json(self.home, "not_free.json", {
            "nvidia/old": {"reason": "antiga", "ts": now - 40 * 3600},   # 40h > TTL 24h
            "nvidia/new": {"reason": "recnte", "ts": now - 3600},        # 1h < TTL
        })
        reserves = self.council.load_reserves(set(), limit=12)
        models = [r.model for r in reserves]
        self.assertIn("nvidia/old", models)   # expirada -> não bloqueia
        self.assertNotIn("nvidia/new", models)  # vigente -> bloqueia

    def test_d2_ttl_configuravel(self):
        """COUNCIL_NOT_FREE_TTL_S=0 desativa o bloqueio por not_free."""
        _write_config(self.home, BASE_CONFIG)
        now = time.time()
        _write_json(self.home, "catalog.json", {"models": [
            {"source": "nvidia", "model": "nvidia/x", "family": "nvidia/x",
             "capabilities": {"reasoning": 90}},
        ]})
        _write_json(self.home, "reliability.json", {})
        _write_json(self.home, "not_free.json", {
            "nvidia/x": {"reason": "recnte", "ts": now - 60},
        })
        os.environ["COUNCIL_NOT_FREE_TTL_S"] = "0"
        try:
            c = _reload_council(self.home)
            reserves = c.load_reserves(set(), limit=12)
            self.assertIn("nvidia/x", [r.model for r in reserves])
        finally:
            os.environ.pop("COUNCIL_NOT_FREE_TTL_S", None)
            self.council = _reload_council(self.home)

    # ------------------------------------------------------------ Teste E ---
    def _run_testa(self, source, model, response=None, exc=None):
        import pool
        importlib.reload(pool)
        client = mock.Mock()
        if exc is not None:
            client.post = mock.AsyncMock(side_effect=exc)
        else:
            client.post = mock.AsyncMock(return_value=response)
        return asyncio.run(pool._testa(client, source, model))

    def test_e_google_endpoint_openai(self):
        """google/gemini usam <base>/openai (adaptação do council.py)."""
        import pool
        importlib.reload(pool)
        captured = {}

        async def fake_post(url, **kw):
            captured["url"] = url
            return _ok()

        client = mock.Mock()
        client.post = fake_post
        rec = asyncio.run(pool._testa(client, "google", "gemini-2.5-flash"))
        self.assertTrue(rec["ok"])
        self.assertTrue(captured["url"].endswith("/v1beta/openai/chat/completions"))

    def test_e_http_401_403(self):
        for status in (401, 403):
            rec = self._run_testa("google", "g", response=FakeResponse(status, {}))
            self.assertFalse(rec["ok"])
            self.assertEqual(rec["classe"], "autenticacao")

    def test_e_http_404(self):
        rec = self._run_testa("nvidia", "n", response=FakeResponse(404, {}))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "nao_encontrado")

    def test_e_http_429(self):
        rec = self._run_testa("nvidia", "n", response=FakeResponse(429, {}))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "rate_limit")

    def test_e_http_500(self):
        rec = self._run_testa("nvidia", "n", response=FakeResponse(503, {}))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "servidor")

    def test_e_timeout(self):
        import httpx
        rec = self._run_testa("nvidia", "n", exc=httpx.TimeoutException("t"))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "timeout")

    def test_e_transporte(self):
        import httpx
        rec = self._run_testa("nvidia", "n", exc=httpx.ConnectError("c"))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "transporte")

    def test_e_resposta_invalida(self):
        rec = self._run_testa("nvidia", "n", response=FakeResponse(200, ValueError("bad json")))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "resposta_invalida")

    def test_e_resposta_vazia(self):
        rec = self._run_testa("nvidia", "n",
                              response=FakeResponse(200, {"choices": [{"message": {"content": ""}}]}))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "resposta_vazia")

    def test_e_sem_credencial(self):
        os.environ.pop("NVIDIA_API_KEY", None)
        import pool
        importlib.reload(pool)
        client = mock.Mock()
        rec = asyncio.run(pool._testa(client, "nvidia", "n"))
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["classe"], "sem_credencial")

    # ------------------------------------------------------------ Teste F ---
    def test_f_chairman_normal(self):
        """Chairman responde: síntese usa o chairman."""
        _write_config(self.home, BASE_CONFIG)
        members, chairman, _ = self.council.load_council_members(4, return_log=True)

        async def fake_chat(client, m, messages, **kw):
            return "resp", None

        with mock.patch.object(self.council, "_chat", side_effect=fake_chat):
            result = asyncio.run(self.council.run_council("q", top_n=4))
        self.assertEqual(result.chairman_model, chairman.model)
        self.assertEqual(result.calls, len(result.stage1) + len(result.stage2) + 1)

    def test_f_chairman_falha_substituto(self):
        """Chairman falha no estágio 1: próximo membro vira chairman substituto."""
        _write_config(self.home, BASE_CONFIG)
        members, chairman, _ = self.council.load_council_members(4, return_log=True)

        async def fake_chat(client, m, messages, **kw):
            if m.model == chairman.model:
                raise RuntimeError("chairman falhou")
            return "resp", None

        with mock.patch.object(self.council, "_chat", side_effect=fake_chat):
            result = asyncio.run(self.council.run_council("q", top_n=4))
        self.assertIn("chairman substituto", result.chairman_model)

    def test_f_todos_falham(self):
        _write_config(self.home, BASE_CONFIG)
        self.council.load_council_members(4)

        async def fake_chat(client, m, messages, **kw):
            raise RuntimeError("falhou")

        with mock.patch.object(self.council, "_chat", side_effect=fake_chat):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.council.run_council("q", top_n=4))

    def test_f_swap_conta_reserva(self):
        """Swap por 429 registra a reserva usada."""
        _write_config(self.home, BASE_CONFIG)
        now = time.time()
        _write_json(self.home, "catalog.json", {"models": [
            {"source": "google", "model": "gemini-reserva", "family": "gemini-reserva",
             "capabilities": {"reasoning": 90}},
        ]})
        _write_json(self.home, "reliability.json", {})
        _write_json(self.home, "not_free.json", {})

        members, chairman, _ = self.council.load_council_members(4, return_log=True)

        calls = {"n": 0}

        async def fake_chat(client, m, messages, **kw):
            calls["n"] += 1
            if m.model == chairman.model:
                import httpx
                req = mock.Mock()
                resp = mock.Mock()
                resp.status_code = 429
                raise httpx.HTTPStatusError("429", request=req, response=resp)
            return "resp", None

        with mock.patch.object(self.council, "_chat", side_effect=fake_chat):
            result = asyncio.run(self.council.run_council("q", top_n=4))
        self.assertTrue(result.swaps)
        self.assertIn("gemini-reserva", result.reserves_used)

    # ------------------------------------------------------------ Teste G ---
    def test_g_obs_regressao_formato(self):
        """Campos novos não quebram o formato JSON existente."""
        _write_config(self.home, BASE_CONFIG)

        async def fake_chat(client, m, messages, **kw):
            return "resp", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

        with mock.patch.object(self.council, "_chat", side_effect=fake_chat):
            result = asyncio.run(self.council.run_council("q", top_n=4))
        d = json.loads(self.council.to_json(result))
        for k in ("query", "started_at", "stage1", "stage2", "chairman_model",
                  "final", "total_elapsed_s", "swaps"):
            self.assertIn(k, d)
        for k in ("deduped", "reserves_used", "stage_times", "calls", "tokens"):
            self.assertIn(k, d)
        self.assertEqual(d["tokens"]["input"], 10 * (len(result.stage1) + len(result.stage2) + 1))
        md = self.council.to_markdown(result)
        self.assertIn("Chamadas:", md)

    def test_g_provider_nos_resultados(self):
        _write_config(self.home, BASE_CONFIG)

        async def fake_chat(client, m, messages, **kw):
            return "resp", None

        with mock.patch.object(self.council, "_chat", side_effect=fake_chat):
            result = asyncio.run(self.council.run_council("q", top_n=4))
        for s in result.stage1:
            self.assertTrue(s.provider)


if __name__ == "__main__":
    unittest.main(verbosity=2)
