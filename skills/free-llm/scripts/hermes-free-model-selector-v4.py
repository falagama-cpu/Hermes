#!/usr/bin/env python3
"""
Hermes Agent — Free LLM Multi-Provider Selector
================================================

Fontes monitoradas:
  1. OpenRouter
  2. Nous Research / Nous Portal
  3. Cloudflare Workers AI
  4. NVIDIA NIM / NVIDIA API Catalog
  5. Google AI Studio (Gemini, free tier da chave)

Objetivo:
  MAIN    -> melhor LLM geral para o Hermes
  MOA     -> LLM diferente da MAIN, privilegiando reasoning/revisão
  F1      -> fallback especializado em coding/agent/tool-use
  F2      -> fallback especializado em reasoning/analysis
  F3      -> fallback especializado em long-context/general

Regras:
  - Nunca repetir a mesma família de LLM.
  - Tentar distribuir as fontes/provedores.
  - Não colocar a mesma LLM via OpenRouter e Nous, por exemplo.
  - Fallbacks precisam ter capacidades complementares.
  - Respeitar somente modelos gratuitos/elegíveis.
  - Nunca sobrescrever o restante do config.yaml.
  - Fazer backup antes de alterar.
  - Registrar seleção e motivo no histórico.

IMPORTANTE SOBRE "FREE":
  OpenRouter e Nous expõem preço nos catálogos.
  Cloudflare Workers AI possui modelos disponíveis no plano Free, mas
  disponibilidade/limites dependem da conta.
  NVIDIA mantém uma lista de "Free Endpoint" que muda com o tempo.

  Para NVIDIA, o script usa uma allowlist configurável em:
    NVIDIA_FREE_MODELS

  Para Cloudflare, por padrão considera modelos @cf de geração de texto
  elegíveis para a cota Free da conta. Se quiser uma allowlist rígida:
    CLOUDFLARE_FREE_MODELS

Credenciais:
  OPENROUTER_API_KEY
  NOUS_API_KEY / NOUS_PORTAL_API_KEY / NOUS_TOKEN
  CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID
  NVIDIA_API_KEY
  GOOGLE_API_KEY (ou GEMINI_API_KEY)

Coloque em $HERMES_HOME/.env (perfil: ~/.hermes/profiles/<perfil>/.env).

Exemplos:
  HERMES_HOME=~/.hermes/profiles/<perfil> python3 hermes-free-model-selector-v4.py --dry-run
  HERMES_HOME=~/.hermes/profiles/<perfil> python3 hermes-free-model-selector-v4.py --force

Agendamento: use install_free_model_selection.sh (cria o job no cron do Hermes).

Dependência:
  pip3 install pyyaml
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import aa_scores  # noqa: E402  ranking por benchmarks da Artificial Analysis

# Peso do benchmark AA no score quando o modelo tem dados na AA (0-1).
AA_WEIGHT = float(os.environ.get("HERMES_AA_WEIGHT", "0.85"))
# Modelos SEM benchmark na AA perdem estes pontos (dado medido > heurística).
AA_MISSING_PENALTY = float(os.environ.get("HERMES_AA_MISSING_PENALTY", "10"))
_AA_STATUS: Dict[str, Any] = {"used": False}


try:
    import yaml
except ImportError:
    print("ERRO: PyYAML não instalado. Execute: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)


# =============================================================================
# PATHS / CONFIG
# =============================================================================

def _platform_default_home() -> Path:
    """Home padrão do Hermes por plataforma (igual ao hermes_constants):
    Windows = %LOCALAPPDATA%\\hermes; Linux/macOS = ~/.hermes."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes"
    return Path.home() / ".hermes"


def _default_home() -> Path:
    # Instalado em <perfil>/scripts/ → o perfil dono é o diretório pai.
    owner = Path(__file__).resolve().parent.parent
    if (owner / "config.yaml").exists():
        return owner
    return _platform_default_home()


HERMES_HOME = Path(os.environ.get("HERMES_HOME") or _default_home())

CONFIG_FILE = HERMES_HOME / "config.yaml"
ENV_FILE = HERMES_HOME / ".env"

STATE_DIR = HERMES_HOME / "model-selector"
STATE_FILE = STATE_DIR / "state.json"
HISTORY_FILE = STATE_DIR / "history.jsonl"
LOG_FILE = STATE_DIR / "selector.log"
CATALOG_JSON = STATE_DIR / "catalog.json"
CATALOG_YAML = STATE_DIR / "catalog.yaml"
RELIABILITY_FILE = STATE_DIR / "reliability.json"
# Modelos que o provedor recusou por PLANO (ex.: Cloudflare 5035 "not available on
# the Workers Free plan"): não são free para esta conta. Revalidados a cada 7 dias.
NOT_FREE_FILE = STATE_DIR / "not_free.json"
NOT_FREE_TTL_S = 7 * 24 * 3600


def _load_not_free() -> Dict[str, Any]:
    try:
        data = json.loads(NOT_FREE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    now = time.time()
    return {k: v for k, v in data.items() if now - v.get("ts", 0) < NOT_FREE_TTL_S}


def mark_not_free(model: Optional[Dict[str, Any]], reason: str) -> None:
    if not model:
        return
    data = _load_not_free()
    data[model["_model_id"]] = {"reason": reason, "ts": time.time()}
    NOT_FREE_FILE.parent.mkdir(parents=True, exist_ok=True)
    NOT_FREE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logging.warning("NÃO-FREE (excluído por 7 dias): %s — %s", model["_model_id"], reason)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
NOUS_BASE_URL = os.environ.get(
    "NOUS_BASE_URL",
    "https://inference-api.nousresearch.com/v1",
)
CLOUDFLARE_API_BASE = "https://api.cloudflare.com/client/v4"
NVIDIA_BASE_URL = os.environ.get(
    "NVIDIA_BASE_URL",
    "https://integrate.api.nvidia.com/v1",
)

MIN_CONTEXT = int(os.environ.get("HERMES_MIN_CONTEXT", "65536"))
FALLBACK_COUNT = int(os.environ.get("HERMES_FALLBACK_COUNT", "3"))
MIN_IMPROVEMENT = float(os.environ.get("HERMES_MIN_IMPROVEMENT", "8"))

# --- Confiabilidade (persistente entre execuções) ---
# Um modelo pode ter ótimo score por habilidade/contexto mas viver dando timeout.
# O tracker reliability.json acumula EMA de latência + falhas consecutivas e:
#   (a) PULA modelos com >= REL_DEAD_FAILS falhas seguidas (quarentena)
#   (b) PENALIZA o score por latência alta e por falhas recentes
# Zera a contagem de falhas quando o modelo volta a responder 200/429.
REL_DEAD_FAILS = int(os.environ.get("HERMES_REL_DEAD_FAILS", "3"))
REL_LAT_SOFT_MS = float(os.environ.get("HERMES_REL_LAT_SOFT_MS", "8000"))   # acima disto começa a penalizar
REL_LAT_HARD_MS = float(os.environ.get("HERMES_REL_LAT_HARD_MS", "45000"))  # latência "ruim" de referência
REL_LAT_MAX_PENALTY = float(os.environ.get("HERMES_REL_LAT_MAX_PENALTY", "25"))  # pts de score máx. descontados
REL_FAIL_PENALTY = float(os.environ.get("HERMES_REL_FAIL_PENALTY", "12"))   # pts por falha recente (×fails, teto 2)
REL_EMA_ALPHA = 0.5   # peso da amostra nova na média móvel de latência

# Distribuição desejada de infraestrutura.
# O score de diversidade usa esta preferência, mas nunca força um provider
# que não tenha um modelo elegível.
SOURCE_ORDER = [
    "openrouter",
    "nous",
    "cloudflare",
    "nvidia",
    "google",
    "openai-codex",
]

SOURCE_BONUS = {
    "openrouter": 0.0,
    "nous": 2.0,
    "cloudflare": 3.0,
    "nvidia": 3.0,
    "google": 0.0,
    "openai-codex": 0.0,
}


# =============================================================================
# NVIDIA FREE ALLOWLIST
# =============================================================================
#
# A lista oficial de "Free Endpoint" do build.nvidia.com muda.
# O script permite substituir totalmente a lista com:
#
# NVIDIA_FREE_MODELS="model/a,model/b,model/c"
#
# Estes nomes correspondem aos modelos que aparecem como Free Endpoint na
# página pública da NVIDIA no momento em que este script foi criado.
#
# Se o endpoint retornar o mesmo modelo com outra grafia, a comparação usa
# normalização e também aceita o ID exato retornado pela API.
#

DEFAULT_NVIDIA_FREE_MODELS = {
    "deepseek-ai/deepseek-v4.1-flash",
    "deepseek/deepseek-v4.1-flash",

    "z-ai/glm-5-3",
    "z-ai/glm-5-3-flash",

    "moonshotai/kimi-k3",

    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-super-120b-a12b",

    "meta/muse-glimmer-30b",
    "meta/llama-3.2-11b-vision-instruct",

    "google/diffusiongemma-26b-a4b-it",
    "google/gemma-4-31b-it",

    "openai/gpt-oss-20b",

    "mistralai/mistral-nemotron",

    "poolside/laguna-xs-2.1",

    "nvidia/ising-calibration-1.5-31b",
    "nvidia/riva-translate-4b-instruct-v2",
    "nvidia/nemotron-voicechat",
}


# =============================================================================
# ENRIQUECIMENTO DE METADADOS ESPARSOS
# =============================================================================
#
# PROBLEMA: o /models da NVIDIA NIM (e o catálogo da Cloudflare) NÃO retornam
# description nem context_length. Sem isso, o scoring por keyword/contexto deixa
# TODO modelo NVIDIA no piso (coding=45, long=30) e a NVIDIA nunca é escolhida —
# viés de METADADOS, não de qualidade. nemotron-ultra-550b perderia de um 2.6b.
#
# SOLUÇÃO: inferir capability-tags e contexto por substring do ID (geral, cobre
# modelos novos da descoberta dinâmica). As tags viram texto no _blob, que o
# scorer de capacidades já lê. Contexto estimado alimenta o score long_context.

# (substring no id, contexto_estimado, [tags de capacidade])
MODEL_ID_HINTS = [
    # NVIDIA / famílias grandes de raciocínio-agente
    ("nemotron-3-ultra", 1_000_000, ["reasoning", "agent", "tool use", "coding"]),
    ("nemotron-3-super", 1_000_000, ["reasoning", "agent", "tool use", "coding"]),
    ("nemotron-3.5-lightning", 1_000_000, ["reasoning", "agent", "tool use"]),
    ("nemotron", 128_000, ["reasoning", "agent", "tool use"]),
    ("kimi-k", 256_000, ["reasoning", "agent", "tool use", "coding"]),
    ("deepseek-v4", 128_000, ["reasoning", "coding", "agent", "tool use"]),
    ("deepseek-r", 128_000, ["reasoning", "thinking", "math"]),
    ("deepseek", 128_000, ["reasoning", "coding"]),
    ("glm-5", 128_000, ["reasoning", "agent", "tool use", "coding"]),
    ("glm-", 128_000, ["reasoning", "coding"]),
    ("muse-glimmer", 128_000, ["reasoning", "agent"]),
    ("gpt-oss", 128_000, ["reasoning", "agent", "tool use", "coding"]),
    ("gemma-4", 128_000, ["instruct", "chat", "reasoning"]),
    ("gemma", 64_000, ["instruct", "chat"]),
    ("llama-3.3", 128_000, ["instruct", "agent", "tool use"]),
    ("llama-3.2", 128_000, ["instruct", "chat"]),
    ("llama", 128_000, ["instruct", "chat"]),
    ("mistral-nemotron", 128_000, ["reasoning", "agent", "tool use"]),
    ("mistral", 128_000, ["instruct", "coding"]),
    ("qwen3-coder", 256_000, ["coding", "agent", "tool use"]),
    ("qwen3", 128_000, ["reasoning", "coding", "agent"]),
    ("qwen", 128_000, ["instruct", "coding"]),
    ("laguna", 256_000, ["coding", "agent", "tool use", "reasoning"]),
    ("longcat", 1_000_000, ["reasoning", "agent", "tool use", "long-horizon"]),
    ("coder", 128_000, ["coding", "agent", "tool use"]),
    ("code", 128_000, ["coding"]),
]


def infer_sparse_metadata(model_id_value: str) -> tuple[int, list[str]]:
    """Retorna (context_estimado, tags) por substring do ID, p/ fontes sem
    description/context (NVIDIA NIM, Cloudflare). (0, []) se nada casar."""
    low = model_id_value.lower()
    for frag, ctx, tags in MODEL_ID_HINTS:
        if frag in low:
            return ctx, tags
    return 0, []


# =============================================================================
# BLOQUEIOS
# =============================================================================

BLOCK_TERMS = (
    "embedding",
    "embed",
    "rerank",
    "reranking",
    "moderation",
    "content-safety",
    "guard",
    "safety-classifier",
    "whisper",
    "text-to-speech",
    "tts",
    "speech-to-text",
    "asr",
    "translation",
    "translate",
    "image-generation",
    "image generation",
    "text-to-image",
    "image-to-image",
    "video",
    "audio-only",
)

TEXT_GENERATION_TERMS = (
    "chat",
    "instruct",
    "instruction",
    "text generation",
    "text-to-text",
    "reasoning",
    "coding",
    "coder",
    "agent",
    "large language",
    "llm",
)


# =============================================================================
# LOG
# =============================================================================

def setup_logging(verbose: bool) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    handlers = [
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


# =============================================================================
# .ENV
# =============================================================================

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ('"', "'")
        ):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


def env_first(*names: str) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


# =============================================================================
# HTTP
# =============================================================================

def http_json(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 40,
    max_retries: int = 4,
    base_delay: float = 2.0,
) -> Any:
    """GET JSON com retry e backoff exponencial + jitter em erros transitórios.

    Retenta 429 (rate limit) e 5xx (500/502/503/504) e falhas de rede/timeout:
    delay = base_delay * 2**tentativa + ruído aleatório [0,1) — o jitter pulveriza
    acessos concorrentes (4 fontes sondando em paralelo não ressincronizam no retry).
    HTTPError não-transitório (4xx exceto 429) sobe na hora, sem retry.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        request = urllib.request.Request(url, headers=headers or {}, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                logging.warning(
                    "HTTP %s em %s — backoff %.1fs (tentativa %d/%d)",
                    exc.code, url, delay, attempt + 1, max_retries,
                )
                time.sleep(delay)
                last_exc = exc
                continue
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"HTTP {exc.code} em {url}: {body[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                logging.warning(
                    "Falha de rede/timeout em %s — backoff %.1fs (tentativa %d/%d)",
                    url, delay, attempt + 1, max_retries,
                )
                time.sleep(delay)
                last_exc = exc
                continue
            raise RuntimeError(f"Falha de rede em {url}: {exc}") from exc

    # Esgotou as tentativas em erro transitório.
    raise RuntimeError(f"Falha definitiva em {url}: {last_exc}")


# =============================================================================
# NORMALIZAÇÃO
# =============================================================================

def normalize(value: Any) -> str:
    text = str(value or "").strip().lower()

    text = text.replace("_", "-")
    text = re.sub(r":free$", "", text)
    text = re.sub(r"\(free\)", "", text)
    text = re.sub(r"\bfree endpoint\b", "", text)
    text = re.sub(r"\bfree\b", "", text)
    text = re.sub(r"\bpreview\b", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    text = re.sub(r"/+", "/", text)

    return text.strip("-/")


def model_id(model: Dict[str, Any]) -> str:
    return str(
        model.get("id")
        or model.get("model")
        or model.get("name")
        or ""
    ).strip()


def model_name(model: Dict[str, Any]) -> str:
    return str(
        model.get("name")
        or model.get("id")
        or model.get("model")
        or ""
    ).strip()


def description(model: Dict[str, Any]) -> str:
    return str(
        model.get("description")
        or model.get("summary")
        or ""
    ).strip()


def context_length(model: Dict[str, Any]) -> int:
    for key in (
        "context_length",
        "context_window",
        "max_context_length",
        "max_model_len",
    ):
        value = model.get(key)

        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                pass

    return 0


def pricing_zero(model: Dict[str, Any]) -> bool:
    pricing = model.get("pricing") or {}

    prompt = pricing.get("prompt")
    completion = pricing.get("completion")

    try:
        if prompt is not None and completion is not None:
            return (
                float(prompt) == 0
                and float(completion) == 0
            )
    except (TypeError, ValueError):
        pass

    # Alguns catálogos retornam string "0".
    for key in ("input", "output", "input_cost", "output_cost"):
        if key in pricing:
            try:
                if float(pricing[key]) != 0:
                    return False
            except (TypeError, ValueError):
                pass

    return False


def family_key(model: Dict[str, Any]) -> str:
    """
    Chave da família da LLM.

    A prioridade é:
      1. canonical_slug/root
      2. model ID normalizado
      3. nome normalizado

    Provider/rota NÃO entra na chave.

    Exemplos que devem colidir:
      openrouter: qwen/qwen3-coder:free
      nous:       qwen/qwen3-coder:free
      nvidia:     qwen/qwen3-coder

    Se a API fornecer canonical_slug/root, ele tem precedência.
    """

    for key in (
        "canonical_slug",
        "canonical_id",
        "root",
    ):
        value = model.get(key)

        if value:
            normalized = normalize(value)

            if normalized:
                return normalized

    mid = normalize(model_id(model))
    name = normalize(model_name(model))

    # Remove prefixos típicos de publisher/provider quando possível.
    if ":" in name:
        name = name.split(":", 1)[-1].strip("-")

    # Para IDs provider/model, usa o caminho inteiro por padrão.
    # Só retiramos :free/preview na normalização.
    if mid:
        return mid

    return name


# =============================================================================
# REPRESENTAÇÃO INTERNA
# =============================================================================

def enrich(
    model: Dict[str, Any],
    source: str,
    provider: Optional[str] = None,
) -> Dict[str, Any]:

    item = copy.deepcopy(model)

    mid = model_id(model)
    item["_source"] = source
    item["_provider"] = provider or source
    item["_model_id"] = mid
    item["_family"] = family_key(model)

    ctx = context_length(model)

    # Fontes com metadados esparsos (NVIDIA NIM, Cloudflare) não trazem contexto
    # nem description: inferimos por substring do ID para não penalizar modelos
    # fortes (nemotron-ultra-550b etc.) que ficariam no piso do scorer.
    inferred_ctx, inferred_tags = infer_sparse_metadata(mid)
    if ctx <= 0 and inferred_ctx > 0:
        ctx = inferred_ctx

    item["_context"] = ctx

    item["_blob"] = " ".join(
        [
            mid,
            model_name(model),
            description(model),
            str(model.get("architecture") or ""),
            str(model.get("supported_parameters") or ""),
            str(model.get("capabilities") or ""),
            str(model.get("tasks") or ""),
            " ".join(inferred_tags),  # tags inferidas alimentam o scorer
        ]
    ).lower()

    item["_caps"] = capabilities(item)

    return item


# =============================================================================
# CAPACIDADES
# =============================================================================

def has_any(text: str, terms: Iterable[str]) -> bool:
    return any(term in text for term in terms)


def capabilities(model: Dict[str, Any]) -> Dict[str, float]:
    text = model.get("_blob") or ""

    coding = 45.0
    reasoning = 45.0
    agentic = 40.0
    tools = 40.0
    long_context = 30.0
    general = 50.0
    multimodal = 0.0

    if has_any(text, (
        "coder",
        "coding",
        "code",
        "programming",
        "software engineering",
        "developer",
        "swe",
        "terminal",
        "repository",
    )):
        coding += 28

    if has_any(text, (
        "reasoning",
        "reasoner",
        "thinking",
        "think",
        "math",
        "logic",
        "problem solving",
        "analysis",
        "planning",
    )):
        reasoning += 28

    if has_any(text, (
        "agent",
        "agentic",
        "tool use",
        "tool-use",
        "function calling",
        "function-call",
        "computer use",
        "long-horizon",
    )):
        agentic += 30
        tools += 22

    supported = str(
        model.get("supported_parameters") or ""
    ).lower()

    if has_any(supported, (
        "tools",
        "tool_choice",
        "parallel_tool_calls",
    )):
        tools += 20
        agentic += 12

    ctx = int(model.get("_context") or 0)

    if ctx >= 1_000_000:
        long_context += 50
    elif ctx >= 500_000:
        long_context += 40
    elif ctx >= 262_000:
        long_context += 32
    elif ctx >= 200_000:
        long_context += 27
    elif ctx >= 128_000:
        long_context += 20
    elif ctx >= 64_000:
        long_context += 12

    if has_any(text, (
        "instruct",
        "instruction",
        "chat",
        "assistant",
        "general purpose",
    )):
        general += 15

    if has_any(text, (
        "multimodal",
        "vision",
        "image-to-text",
        "visual",
        "vlm",
    )):
        multimodal += 50

    return {
        "coding": min(coding, 100),
        "reasoning": min(reasoning, 100),
        "agentic": min(agentic, 100),
        "tools": min(tools, 100),
        "long_context": min(long_context, 100),
        "general": min(general, 100),
        "multimodal": min(multimodal, 100),
    }


def _score_role_raw(model: Dict[str, Any], role: str) -> float:
    caps = model["_caps"]

    if role == "main":
        return (
            caps["coding"] * 0.28
            + caps["agentic"] * 0.24
            + caps["reasoning"] * 0.20
            + caps["tools"] * 0.16
            + caps["long_context"] * 0.07
            + caps["general"] * 0.05
        )

    if role == "moa":
        return (
            caps["reasoning"] * 0.44
            + caps["coding"] * 0.14
            + caps["agentic"] * 0.15
            + caps["tools"] * 0.10
            + caps["long_context"] * 0.12
            + caps["general"] * 0.05
        )

    if role == "coding":
        return (
            caps["coding"] * 0.43
            + caps["agentic"] * 0.24
            + caps["tools"] * 0.20
            + caps["reasoning"] * 0.08
            + caps["general"] * 0.05
        )

    if role == "reasoning":
        return (
            caps["reasoning"] * 0.52
            + caps["coding"] * 0.13
            + caps["agentic"] * 0.10
            + caps["tools"] * 0.08
            + caps["long_context"] * 0.12
            + caps["general"] * 0.05
        )

    if role == "long_context":
        return (
            caps["long_context"] * 0.45
            + caps["general"] * 0.20
            + caps["reasoning"] * 0.15
            + caps["coding"] * 0.08
            + caps["agentic"] * 0.07
            + caps["tools"] * 0.05
        )

    return (
        caps["general"] * 0.30
        + caps["coding"] * 0.20
        + caps["reasoning"] * 0.20
        + caps["agentic"] * 0.15
        + caps["tools"] * 0.10
        + caps["long_context"] * 0.05
    )


# Tracker de confiabilidade ativo durante a seleção (setado no run()). O score
# final = score por habilidade/contexto MENOS a penalidade por latência/falhas.
_REL: Dict[str, Any] = {}


def score_role(model: Dict[str, Any], role: str) -> float:
    """Score do papel JÁ descontada a penalidade de confiabilidade (latência alta
    / falhas recentes). Com dados da Artificial Analysis o score é majoritariamente
    o benchmark medido (AA_WEIGHT); a heurística por palavras-chave só completa.
    Se a AA está ativa e o modelo não tem benchmark, perde AA_MISSING_PENALTY."""
    base = _score_role_raw(model, role)
    aa = model.get("_aa")
    if aa:
        aa_val = aa_scores.role_score(aa, role, model["_caps"]["long_context"])
        base = AA_WEIGHT * aa_val + (1 - AA_WEIGHT) * base
    elif _AA_STATUS.get("used"):
        base -= AA_MISSING_PENALTY
    return base - reliability_penalty(_REL, model)


# =============================================================================
# FILTROS
# =============================================================================

def blocked(model: Dict[str, Any]) -> bool:
    text = model.get("_blob") or ""

    return has_any(text, BLOCK_TERMS)


def is_text_model(model: Dict[str, Any]) -> bool:
    text = model.get("_blob") or ""

    if has_any(text, TEXT_GENERATION_TERMS):
        return True

    # OpenAI-compatible model catalogs frequentemente não têm task metadata.
    # Para modelos cujo ID não sugere outra modalidade, permitimos.
    return True


def valid_context(model: Dict[str, Any]) -> bool:
    ctx = int(model.get("_context") or 0)

    # Se o provider não informar contexto, não eliminamos automaticamente.
    return ctx == 0 or ctx >= MIN_CONTEXT


def base_eligible(model: Dict[str, Any]) -> bool:
    return (
        bool(model.get("_model_id"))
        and not blocked(model)
        and is_text_model(model)
        and valid_context(model)
    )


# =============================================================================
# OPENROUTER
# =============================================================================

def fetch_openrouter() -> List[Dict[str, Any]]:
    key = env_first("OPENROUTER_API_KEY")

    if not key:
        logging.warning("OpenRouter: OPENROUTER_API_KEY ausente.")
        return []

    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
    }

    try:
        payload = http_json(
            OPENROUTER_MODELS_URL,
            headers=headers,
        )
    except Exception as exc:
        logging.warning("OpenRouter indisponível: %s", exc)
        return []

    raw_models = payload.get("data", [])

    result = []

    for raw in raw_models:
        item = enrich(raw, "openrouter")

        if not pricing_zero(raw) and ":free" not in model_id(raw).lower():
            continue

        if base_eligible(item):
            result.append(item)

    logging.info(
        "OpenRouter: %d modelos Free elegíveis.",
        len(result),
    )

    return result


# =============================================================================
# NOUS RESEARCH
# =============================================================================

def fetch_nous() -> List[Dict[str, Any]]:
    key = env_first(
        "NOUS_API_KEY",
        "NOUS_PORTAL_API_KEY",
        "NOUS_TOKEN",
    )

    headers = {
        "Accept": "application/json",
        "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
    }

    # /models é público, mas sem chave os modelos não respondem no probe/runtime:
    # listá-los só gera seleções que falham ("sem credencial").
    if not key:
        logging.warning("Nous: NOUS_API_KEY ausente — fonte ignorada.")
        return []
    headers["Authorization"] = f"Bearer {key}"

    url = NOUS_BASE_URL.rstrip("/") + "/models"

    try:
        payload = http_json(
            url,
            headers=headers,
        )
    except Exception as exc:
        logging.warning("Nous indisponível: %s", exc)
        return []

    raw_models = payload.get("data", [])

    if not isinstance(raw_models, list):
        logging.warning("Nous: resposta /models inesperada.")
        return []

    result = []

    for raw in raw_models:
        item = enrich(raw, "nous")

        # Nous pode publicar :free ou pricing=0.
        if not pricing_zero(raw) and ":free" not in model_id(raw).lower():
            continue

        if base_eligible(item):
            result.append(item)

    logging.info(
        "Nous Research: %d modelos Free elegíveis.",
        len(result),
    )

    return result


# =============================================================================
# CLOUDFLARE WORKERS AI
# =============================================================================

def parse_csv(value: Optional[str]) -> Set[str]:
    if not value:
        return set()

    return {
        normalize(part)
        for part in value.split(",")
        if normalize(part)
    }


def fetch_cloudflare() -> List[Dict[str, Any]]:
    token = env_first("CLOUDFLARE_API_TOKEN")
    account = env_first("CLOUDFLARE_ACCOUNT_ID")

    if not token or not account:
        logging.warning(
            "Cloudflare: CLOUDFLARE_API_TOKEN ou "
            "CLOUDFLARE_ACCOUNT_ID ausente."
        )
        return []

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
    }

    allowlist = parse_csv(
        os.environ.get("CLOUDFLARE_FREE_MODELS")
    )

    all_models: List[Dict[str, Any]] = []

    # API paginada.
    page = 1

    while page <= 20:
        query = urllib.parse.urlencode(
            {
                "page": page,
                "per_page": 100,
                "hide_experimental": "true",
            }
        )

        url = (
            f"{CLOUDFLARE_API_BASE}/accounts/"
            f"{urllib.parse.quote(account, safe='')}/ai/models/search"
            f"?{query}"
        )

        try:
            payload = http_json(
                url,
                headers=headers,
            )
        except Exception as exc:
            logging.warning(
                "Cloudflare: falha ao consultar catálogo: %s",
                exc,
            )
            break

        result_page = payload.get("result", [])

        if not isinstance(result_page, list) or not result_page:
            break

        all_models.extend(result_page)

        if len(result_page) < 100:
            break

        page += 1

    result = []

    for raw in all_models:
        # Cloudflare usa IDs como @cf/meta/llama...
        mid = str(
            raw.get("name")
            or raw.get("id")
            or ""
        )

        task_text = " ".join(
            [
                mid,
                str(raw.get("description") or ""),
                str(raw.get("task") or ""),
                str(raw.get("task_name") or ""),
                str(raw.get("type") or ""),
            ]
        ).lower()

        # Workers AI hosted models usam @cf/.
        if not mid.startswith("@cf/"):
            continue

        # Se o usuário especificou allowlist, ela manda.
        normalized_id = normalize(mid)

        if allowlist and normalized_id not in allowlist:
            continue

        # Não incluir modalidades que não servem ao Hermes.
        if has_any(task_text, BLOCK_TERMS):
            continue

        # Preferimos modelos que tenham algum indício textual de geração.
        # Se o catálogo não informar task, deixamos passar.
        if (
            not has_any(task_text, TEXT_GENERATION_TERMS)
            and raw.get("task")
        ):
            continue

        raw_copy = copy.deepcopy(raw)
        raw_copy["id"] = mid

        item = enrich(
            raw_copy,
            "cloudflare",
            provider="custom:cloudflare-free",
        )

        if base_eligible(item):
            result.append(item)

    logging.info(
        "Cloudflare Workers AI: %d modelos elegíveis.",
        len(result),
    )

    return result


# =============================================================================
# NVIDIA NIM
# =============================================================================

# Cache do probe de descoberta: {id: {"status", "ts", "last_ok"}}.
NVIDIA_PROBE_FILE = STATE_DIR / "nvidia_free_probe.json"
NVIDIA_PROBE_OK_TTL_S = 24 * 3600          # 200/429 recente → não re-sonda
NVIDIA_PROBE_DEAD_TTL_S = 7 * 24 * 3600    # 4xx permanente → pula por 7 dias
NVIDIA_PROBE_GRACE_S = 7 * 24 * 3600       # transiente mantém quem foi OK há < 7d
NVIDIA_PROBE_TIMEOUT = int(os.environ.get("HERMES_NVIDIA_PROBE_TIMEOUT", "20"))
NVIDIA_PROBE_WORKERS = int(os.environ.get("HERMES_NVIDIA_PROBE_WORKERS", "10"))
_NVIDIA_PERMANENT = {400, 401, 402, 403, 404, 410, 422}


def _nvidia_probe_one(key: str, mid: str) -> int:
    """POST /chat/completions mínimo. Retorna HTTP status (0 = rede/timeout)."""
    body = json.dumps({
        "model": mid,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }).encode("utf-8")
    req = urllib.request.Request(
        NVIDIA_BASE_URL.rstrip("/") + "/chat/completions",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=NVIDIA_PROBE_TIMEOUT) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return 0


def nvidia_discover_free(raw_models: List[Dict[str, Any]], key: str) -> Set[str]:
    """Sonda TODO o catálogo /models da NVIDIA e devolve os IDs normalizados
    que respondem como Free Endpoint (200/429). Sem allowlist: modelos novos
    entram sozinhos; os que saem do free tier (404) caem sozinhos.

    - pré-filtro barato por BLOCK_TERMS no ID (guard/safety/video/tts...)
    - cache em nvidia_free_probe.json: OK < 24h e 4xx < 7d não re-sondam
    - transiente (timeout/5xx): mantém se teve OK nos últimos 7 dias ou se
      está na seed DEFAULT_NVIDIA_FREE_MODELS (o probe pré-gravação re-valida)
    """
    from concurrent.futures import ThreadPoolExecutor

    try:
        cache = json.loads(NVIDIA_PROBE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}

    seed = {normalize(x) for x in DEFAULT_NVIDIA_FREE_MODELS}
    now = time.time()

    ids = sorted({
        model_id(r) for r in raw_models
        if model_id(r) and not has_any(model_id(r).lower(), BLOCK_TERMS)
    })

    to_probe: List[str] = []
    for mid in ids:
        e = cache.get(mid) or {}
        age = now - float(e.get("ts") or 0)
        st = e.get("status")
        if st in (200, 429) and age < NVIDIA_PROBE_OK_TTL_S:
            continue
        if st in _NVIDIA_PERMANENT and age < NVIDIA_PROBE_DEAD_TTL_S:
            continue
        to_probe.append(mid)

    if to_probe:
        with ThreadPoolExecutor(max_workers=max(1, NVIDIA_PROBE_WORKERS)) as ex:
            statuses = list(ex.map(lambda m: _nvidia_probe_one(key, m), to_probe))
        for mid, st in zip(to_probe, statuses):
            e = cache.setdefault(mid, {})
            e["status"] = st
            e["ts"] = now
            if st in (200, 429):
                e["last_ok"] = now

    # Remove do cache IDs que sumiram do /models.
    cache = {k: v for k, v in cache.items() if k in ids}

    free: Set[str] = set()
    transient_kept: List[str] = []
    for mid in ids:
        e = cache.get(mid) or {}
        st = e.get("status")
        norm = normalize(mid)
        if st in (200, 429):
            free.add(norm)
        elif st not in _NVIDIA_PERMANENT:
            recent_ok = now - float(e.get("last_ok") or 0) < NVIDIA_PROBE_GRACE_S
            if recent_ok or norm in seed:
                free.add(norm)
                transient_kept.append(mid)

    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        NVIDIA_PROBE_FILE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    except OSError:
        pass

    new_vs_seed = sorted(m for m in ids if normalize(m) in free and normalize(m) not in seed)
    logging.info(
        "NVIDIA discovery: %d IDs no /models, %d sondados agora, %d free "
        "(%d transientes mantidos); fora da seed: %s",
        len(ids), len(to_probe), len(free), len(transient_kept),
        ", ".join(new_vs_seed) or "-",
    )
    return free


def fetch_nvidia() -> List[Dict[str, Any]]:
    key = env_first("NVIDIA_API_KEY")

    if not key:
        logging.warning("NVIDIA: NVIDIA_API_KEY ausente.")
        return []

    headers = {
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
        "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
    }

    url = NVIDIA_BASE_URL.rstrip("/") + "/models"

    try:
        payload = http_json(
            url,
            headers=headers,
        )
    except Exception as exc:
        logging.warning("NVIDIA NIM indisponível: %s", exc)
        return []

    raw_models = payload.get("data", [])

    if not isinstance(raw_models, list):
        logging.warning("NVIDIA: resposta /models inesperada.")
        return []

    configured_allowlist = parse_csv(
        os.environ.get("NVIDIA_FREE_MODELS")
    )

    # Descoberta dinâmica (padrão): TODO o /models é sondado e entra quem
    # responde como Free Endpoint. NVIDIA_FREE_MODELS (env) = allowlist rígida;
    # HERMES_NVIDIA_DISCOVERY=0 volta ao comportamento antigo (só a seed).
    discovery = (
        not configured_allowlist
        and os.environ.get("HERMES_NVIDIA_DISCOVERY", "1") != "0"
    )

    if discovery:
        allowlist = nvidia_discover_free(raw_models, key)
    else:
        allowlist = configured_allowlist or {
            normalize(x)
            for x in DEFAULT_NVIDIA_FREE_MODELS
        }

    result = []

    for raw in raw_models:
        mid = model_id(raw)
        normalized = normalize(mid)

        # A API /models não informa preço: o gate é o probe (discovery)
        # ou a allowlist (modo rígido).
        if normalized not in allowlist:
            continue

        item = enrich(
            raw,
            "nvidia",
            provider="nvidia",
        )

        if base_eligible(item):
            result.append(item)

    logging.info(
        "NVIDIA NIM: %d modelos Free elegíveis.",
        len(result),
    )

    return result


# =============================================================================
# UNIÃO DAS FONTES
# =============================================================================

# =============================================================================
# GOOGLE AI STUDIO (Gemini, plano gratuito da chave)
# =============================================================================
# Não há preço no catálogo: "free" = a chave está no free tier e o modelo responde.
# Modelo pago para a chave → probe devolve 429 com "limit: 0" → mark_not_free().

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_OPENAI_BASE = GOOGLE_API_BASE + "/openai"
_GOOGLE_SKIP = ("tts", "image", "transcribe", "omni", "nano-banana", "embedding",
                "customtools", "-live", "robotics", "computer-use", "aqa", "learnlm")


def google_key() -> Optional[str]:
    # Valores curtos (placeholder) não são chave: o .env pode ter GEMINI_API_KEY lixo.
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        value = (os.environ.get(name) or "").strip()
        if len(value) >= 20:
            return value
    return None


def google_key_env() -> str:
    for name in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        if len((os.environ.get(name) or "").strip()) >= 20:
            return name
    return "GOOGLE_API_KEY"


def fetch_google() -> List[Dict[str, Any]]:
    key = google_key()
    if not key:
        logging.warning("Google: GOOGLE_API_KEY ausente.")
        return []

    headers = {
        "x-goog-api-key": key,
        "Accept": "application/json",
        "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
    }
    raw_models: List[Dict[str, Any]] = []
    page = ""
    try:
        for _ in range(10):
            url = GOOGLE_API_BASE + "/models?pageSize=200" + (
                "&pageToken=" + urllib.parse.quote(page) if page else "")
            payload = http_json(url, headers=headers)
            raw_models.extend(payload.get("models") or [])
            page = payload.get("nextPageToken") or ""
            if not page:
                break
    except Exception as exc:
        logging.warning("Google indisponível: %s", exc)
        return []

    result = []
    for raw in raw_models:
        mid = str(raw.get("name") or "").removeprefix("models/")
        low = mid.lower()
        if (not low.startswith("gemini-") or low.endswith("-latest")
                or any(t in low for t in _GOOGLE_SKIP)
                or "generateContent" not in (raw.get("supportedGenerationMethods") or [])):
            continue
        item = enrich({
            "id": mid,
            "name": raw.get("displayName") or mid,
            "description": raw.get("description") or "",
            "context_length": raw.get("inputTokenLimit") or 0,
        }, "google")
        if base_eligible(item):
            result.append(item)

    logging.info("Google AI Studio: %d modelos Gemini elegíveis (free tier validado no probe).",
                 len(result))
    return result


# =============================================================================
# OPENAI via login ChatGPT (provider openai-codex do Hermes, OAuth)
# =============================================================================
# Sem API key: usa o access_token que o Hermes guarda em $HERMES_HOME/auth.json,
# SÓ LEITURA — nunca renova (gastar o refresh_token rotativo derrubaria o login
# do Hermes). Disponibilidade vem de /wham/usage (GET grátis, não consome cota);
# o plano free do ChatGPT tem cota mensal pequena para o Codex.

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_CODEX_STATUS: Dict[str, Any] = {}


def _codex_token() -> Optional[Tuple[str, Dict[str, str]]]:
    """(access_token, headers) do login openai-codex, ou None se ausente/expirado."""
    import base64
    try:
        auth = json.loads((HERMES_HOME / "auth.json").read_text())
    except (OSError, ValueError):
        return None
    tokens = ((auth.get("providers") or {}).get("openai-codex") or {}).get("tokens") or {}
    candidates = [tokens.get("access_token")]
    for entry in (auth.get("credential_pool") or {}).get("openai-codex") or []:
        if isinstance(entry, dict):
            candidates.append(entry.get("access_token") or entry.get("runtime_api_key"))
    for token in candidates:
        if not isinstance(token, str) or token.count(".") < 2:
            continue
        try:
            part = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        except (ValueError, TypeError):
            continue
        if claims.get("exp", 0) - time.time() < 300:
            continue
        info = claims.get("https://api.openai.com/auth") or {}
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": "codex-cli",
            "originator": "codex_cli_rs",
        }
        if info.get("chatgpt_account_id"):
            headers["ChatGPT-Account-ID"] = info["chatgpt_account_id"]
        _CODEX_STATUS["plan"] = info.get("chatgpt_plan_type") or "?"
        return token, headers
    return None


def codex_usage_ok(headers: Dict[str, str]) -> bool:
    """Consulta a cota sem gastar requisição. Grava o estado em _CODEX_STATUS."""
    try:
        usage = http_json(CODEX_USAGE_URL, headers=headers, max_retries=1)
    except Exception as exc:
        _CODEX_STATUS["error"] = str(exc)[:200]
        return False
    rl = usage.get("rate_limit") or {}
    windows = [w for w in (rl.get("primary_window"), rl.get("secondary_window")) if w]
    _CODEX_STATUS["used_percent"] = max((w.get("used_percent") or 0 for w in windows), default=0)
    resets = [w.get("reset_at") for w in windows if (w.get("used_percent") or 0) >= 100]
    if resets:
        _CODEX_STATUS["reset_at"] = max(resets)
    return bool(rl.get("allowed", True)) and not rl.get("limit_reached")


def fetch_openai_codex() -> List[Dict[str, Any]]:
    cred = _codex_token()
    if not cred:
        logging.warning("OpenAI (login ChatGPT): sem token válido em auth.json "
                        "(faça login: hermes auth add openai-codex) — fonte ignorada.")
        return []
    _, headers = cred
    if not codex_usage_ok(headers):
        reset = _CODEX_STATUS.get("reset_at")
        when = dt.datetime.fromtimestamp(reset).strftime("%d/%m %H:%M") if reset else "?"
        logging.warning("OpenAI (login ChatGPT, plano %s): cota do Codex esgotada "
                        "(%s%%) até %s — fonte ignorada nesta execução.",
                        _CODEX_STATUS.get("plan"), _CODEX_STATUS.get("used_percent"), when)
        return []
    try:
        payload = http_json(CODEX_BASE_URL + "/models?client_version=99.0.0", headers=headers)
    except Exception as exc:
        logging.warning("OpenAI (login ChatGPT): catálogo indisponível: %s", exc)
        return []

    result = []
    for raw in payload.get("models") or []:
        slug = raw.get("slug") or ""
        if not slug or raw.get("visibility") != "list":
            continue
        item = enrich({
            "id": slug,
            "name": raw.get("display_name") or slug,
            "description": raw.get("description") or "",
            "context_length": raw.get("context_window") or 0,
        }, "openai-codex")
        if base_eligible(item):
            result.append(item)
    logging.info("OpenAI (login ChatGPT, plano %s, cota usada %s%%): %d modelos elegíveis.",
                 _CODEX_STATUS.get("plan"), _CODEX_STATUS.get("used_percent"), len(result))
    return result


def collect_models() -> List[Dict[str, Any]]:
    models: List[Dict[str, Any]] = []

    collectors = (
        fetch_openrouter,
        fetch_nous,
        fetch_cloudflare,
        fetch_nvidia,
        fetch_google,
        fetch_openai_codex,
    )

    for collector in collectors:
        try:
            models.extend(collector())
        except Exception:
            logging.exception(
                "Erro inesperado no collector %s",
                collector.__name__,
            )

    # Remover duplicata exata de source+id.
    unique: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for model in models:
        key = (
            str(model["_source"]),
            str(model["_model_id"]),
        )

        current = unique.get(key)

        if current is None:
            unique[key] = model
            continue

        # Mantém a versão com maior score geral.
        if score_role(model, "main") > score_role(current, "main"):
            unique[key] = model

    models = list(unique.values())

    logging.info(
        "Total consolidado: %d rotas/modelos elegíveis.",
        len(models),
    )

    return models


# =============================================================================
# CONFIABILIDADE (persistente) — latência + falhas entre execuções
# =============================================================================

def load_reliability() -> Dict[str, Any]:
    try:
        return json.loads(RELIABILITY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_reliability(rel: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        RELIABILITY_FILE.write_text(
            json.dumps(rel, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def _rel_key(model: Dict[str, Any]) -> str:
    """Chave por fonte+família: a mesma família em providers distintos tem
    confiabilidade distinta (um Qwen via Nous pode ser rápido e via NVIDIA lento)."""
    return f"{model['_source']}::{model['_family']}"


def record_reliability(rel: Dict[str, Any], model: Dict[str, Any],
                       *, ok: bool, latency_ms: Optional[float]) -> None:
    """Atualiza EMA de latência + contador de falhas consecutivas do modelo."""
    k = _rel_key(model)
    e = rel.setdefault(k, {"fails": 0, "ema_ms": None, "last_ok": None, "samples": 0})
    e["samples"] = int(e.get("samples", 0)) + 1
    if ok:
        e["fails"] = 0
        if latency_ms is not None:
            prev = e.get("ema_ms")
            e["ema_ms"] = (
                float(latency_ms) if prev is None
                else REL_EMA_ALPHA * float(latency_ms) + (1 - REL_EMA_ALPHA) * float(prev)
            )
        e["last_ok"] = dt.datetime.now(dt.timezone.utc).isoformat()
    else:
        e["fails"] = int(e.get("fails", 0)) + 1


def is_quarantined(rel: Dict[str, Any], model: Dict[str, Any]) -> bool:
    """True se o modelo acumulou falhas consecutivas demais (não vale re-sondar)."""
    e = rel.get(_rel_key(model))
    return bool(e) and int(e.get("fails", 0)) >= REL_DEAD_FAILS


def reliability_penalty(rel: Dict[str, Any], model: Dict[str, Any]) -> float:
    """Pontos a DESCONTAR do score por baixa confiabilidade: latência alta
    (EMA entre SOFT e HARD → 0..MAX) + falhas recentes (×fails, teto 2)."""
    e = rel.get(_rel_key(model))
    if not e:
        return 0.0

    penalty = 0.0

    ema = e.get("ema_ms")
    if ema is not None and ema > REL_LAT_SOFT_MS:
        frac = (float(ema) - REL_LAT_SOFT_MS) / max(1.0, REL_LAT_HARD_MS - REL_LAT_SOFT_MS)
        penalty += max(0.0, min(1.0, frac)) * REL_LAT_MAX_PENALTY

    fails = int(e.get("fails", 0))
    if fails > 0:
        penalty += REL_FAIL_PENALTY * min(fails, 2)

    return penalty


# =============================================================================
# CATÁLOGO (P1) — auditoria do que foi encontrado/validado como Free
# =============================================================================

def _free_reason(model: Dict[str, Any]) -> str:
    """Por que este modelo é considerado Free, para auditoria."""
    source = model["_source"]
    mid = model["_model_id"].lower()

    if source == "openrouter":
        if ":free" in mid:
            return "openrouter:suffix-free"
        return "openrouter:pricing-zero"
    if source == "nous":
        if ":free" in mid:
            return "nous:suffix-free"
        return "nous:pricing-zero"
    if source == "nvidia":
        return "nvidia:allowlist-free-endpoint"
    if source == "cloudflare":
        return "cloudflare:workers-ai-free-tier"
    if source == "google":
        return "google:ai-studio-free-tier"
    if source == "openai-codex":
        return f"openai-codex:chatgpt-{_CODEX_STATUS.get('plan', '?')}-plan"
    return "unknown"


def build_catalog(models: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    catalog: List[Dict[str, Any]] = []

    for model in sorted(
        models,
        key=lambda m: (m["_source"], -int(m.get("_context") or 0), m["_model_id"]),
    ):
        caps = model["_caps"]
        catalog.append({
            "source": model["_source"],
            "provider": provider_for_config(model),
            "model": model["_model_id"],
            "family": model["_family"],
            "context": int(model.get("_context") or 0),
            "free_reason": _free_reason(model),
            "aa": model.get("_aa"),
            "capabilities": {
                "coding": round(caps["coding"]),
                "reasoning": round(caps["reasoning"]),
                "agentic": round(caps["agentic"]),
                "tools": round(caps["tools"]),
                "long_context": round(caps["long_context"]),
                "general": round(caps["general"]),
            },
        })

    return catalog


def write_catalog(models: List[Dict[str, Any]]) -> None:
    """Grava catalog.json + catalog.yaml ANTES da seleção (fonte de auditoria)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    catalog = build_catalog(models)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "total": len(catalog),
        "by_source": {
            src: sum(1 for c in catalog if c["source"] == src)
            for src in SOURCE_ORDER
        },
        "min_context": MIN_CONTEXT,
        "models": catalog,
    }

    CATALOG_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    CATALOG_YAML.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    logging.info(
        "Catálogo gravado: %s (%d modelos) e %s",
        CATALOG_JSON, len(catalog), CATALOG_YAML,
    )


# =============================================================================
# FAMÍLIA / ROUTES
# =============================================================================

def group_by_family(
    models: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:

    groups: Dict[str, List[Dict[str, Any]]] = {}

    for model in models:
        groups.setdefault(
            model["_family"],
            [],
        ).append(model)

    return groups


def best_route_for_family(
    models: List[Dict[str, Any]],
    role: str,
    used_sources: Optional[Set[str]] = None,
) -> Optional[Dict[str, Any]]:

    used_sources = used_sources or set()

    def route_score(model: Dict[str, Any]) -> float:
        score = score_role(model, role)

        source = model["_source"]

        # Pequeno bônus para diversidade.
        if source not in used_sources:
            score += SOURCE_BONUS.get(source, 0)

        # Preferência por contexto adequado.
        if model["_context"] >= MIN_CONTEXT:
            score += 3

        return score

    if not models:
        return None

    return max(models, key=route_score)


def family_candidates(
    models: List[Dict[str, Any]],
    role: str,
    used_families: Set[str],
    used_sources: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:

    groups = group_by_family(models)

    result = []

    for family, routes in groups.items():
        if family in used_families:
            continue

        best = best_route_for_family(
            routes,
            role,
            used_sources=used_sources,
        )

        if best:
            candidate = copy.deepcopy(best)
            candidate["_role_score"] = score_role(candidate, role)
            result.append(candidate)

    return result


# =============================================================================
# MAIN
# =============================================================================

def choose_main(
    models: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:

    candidates = family_candidates(
        models,
        "main",
        used_families=set(),
        used_sources=set(),
    )

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda m: (
            m["_role_score"]
            + SOURCE_BONUS.get(m["_source"], 0)
        ),
    )


# =============================================================================
# MOA
# =============================================================================

def _unused_source_candidates(
    models: List[Dict[str, Any]],
    role: str,
    used_families: Set[str],
    used_sources: Set[str],
) -> List[Dict[str, Any]]:
    """Candidatos só de providers ainda não usados (1 agente por provider:
    evita estourar o rate limit de um provider com várias chamadas paralelas
    do MoA/council). Sem candidato em provider novo → aceita repetir."""
    fresh = [m for m in models if m["_source"] not in used_sources]
    return (
        family_candidates(fresh, role, used_families, used_sources)
        or family_candidates(models, role, used_families, used_sources)
    )


def choose_moa(
    models: List[Dict[str, Any]],
    used_families: Set[str],
    used_sources: Set[str],
) -> Optional[Dict[str, Any]]:

    candidates = _unused_source_candidates(
        models,
        "moa",
        used_families,
        used_sources,
    )

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda m: (
            m["_role_score"]
            + (4 if m["_source"] not in used_sources else 0)
        ),
    )


# =============================================================================
# MOA REFERENCE MODELS
# =============================================================================

# Quantos reference models o MoA consulta antes do aggregator (0 = não mexe).
MOA_REFERENCE_COUNT = int(os.environ.get("HERMES_MOA_REFERENCE_COUNT", "2"))


def _vendor(model: Dict[str, Any]) -> str:
    """Fabricante do modelo (prefixo do ID, sem @cf/). laguna-s e laguna-xs
    são famílias distintas mas o mesmo vendor (poolside)."""
    mid = str(model.get("_model_id") or "").lower()
    if mid.startswith("@cf/"):
        mid = mid[4:]
    return mid.split("/", 1)[0] if "/" in mid else mid


def choose_moa_references(
    models: List[Dict[str, Any]],
    used_families: Set[str],
    used_sources: Set[str],
    count: int = MOA_REFERENCE_COUNT,
    used_vendors: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Top-N para moa.reference_models: famílias distintas entre si e do
    MAIN/aggregator (perspectivas diferentes = o ponto do MoA); papel "moa"
    (intel-pesado). Prefere fabricante inédito (+8) e provider inédito (+3);
    repete fabricante só se não houver alternativa."""
    selected: List[Dict[str, Any]] = []
    families = set(used_families)
    sources = set(used_sources)
    vendors = set(used_vendors or set())

    for _ in range(max(0, count)):
        candidates = _unused_source_candidates(models, "moa", families, sources)
        if not candidates:
            break
        chosen = max(
            candidates,
            key=lambda m: (
                m["_role_score"]
                + (8 if _vendor(m) not in vendors else 0)
                + (3 if m["_source"] not in sources else 0)
            ),
        )
        selected.append(chosen)
        families.add(chosen["_family"])
        sources.add(chosen["_source"])
        vendors.add(_vendor(chosen))

    return selected


# =============================================================================
# FALLBACKS
# =============================================================================

FALLBACK_PROFILES = [
    (
        "coding-agent",
        "Coding / Agent / Tool use",
        "coding",
    ),
    (
        "reasoning",
        "Reasoning / Analysis",
        "reasoning",
    ),
    (
        "long-context",
        "Long Context / General",
        "long_context",
    ),
]


def capability_distance(
    a: Dict[str, float],
    b: Dict[str, float],
) -> float:

    keys = (
        "coding",
        "reasoning",
        "agentic",
        "tools",
        "long_context",
        "general",
    )

    return sum(
        abs(a[k] - b[k])
        for k in keys
    ) / len(keys)


def fallback_score(
    model: Dict[str, Any],
    profile: str,
    selected: List[Dict[str, Any]],
    used_sources: Set[str],
) -> float:

    base = score_role(model, profile)

    if model["_source"] not in used_sources:
        base += 5

    if not selected:
        return base

    caps = model["_caps"]

    # Penaliza redundância com o conjunto já escolhido.
    redundancy = 0.0

    for previous in selected:
        redundancy += 100 - capability_distance(
            caps,
            previous["_caps"],
        )

    redundancy /= len(selected)

    # A ideia é preferir capacidades diferentes.
    base -= redundancy * 0.10

    return base


def choose_fallbacks(
    models: List[Dict[str, Any]],
    used_families: Set[str],
    used_sources: Set[str],
) -> List[Dict[str, Any]]:

    selected: List[Dict[str, Any]] = []
    families = set(used_families)
    sources = set(used_sources)

    for profile_name, label, role in FALLBACK_PROFILES:
        candidates = family_candidates(
            models,
            role,
            families,
            sources,
        )

        if not candidates:
            logging.warning(
                "Não foi encontrado fallback para perfil %s.",
                profile_name,
            )
            continue

        ranked = sorted(
            candidates,
            key=lambda model: fallback_score(
                model,
                role,
                selected,
                sources,
            ),
            reverse=True,
        )

        chosen = ranked[0]

        chosen["_fallback_profile"] = profile_name
        chosen["_fallback_label"] = label
        chosen["_fallback_score"] = fallback_score(
            chosen,
            role,
            selected,
            sources,
        )

        selected.append(chosen)
        families.add(chosen["_family"])
        sources.add(chosen["_source"])

        if len(selected) >= FALLBACK_COUNT:
            break

    return selected


# =============================================================================
# CURRENT CONFIG / SWITCH POLICY
# =============================================================================

def load_config() -> Dict[str, Any]:
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            f"Config do Hermes não encontrada: {CONFIG_FILE}"
        )

    data = yaml.safe_load(
        CONFIG_FILE.read_text(encoding="utf-8")
    )

    if data is None:
        data = {}

    if not isinstance(data, dict):
        raise RuntimeError(
            "config.yaml não contém um objeto YAML válido."
        )

    return data


def current_main(config: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    model = config.get("model")

    if not isinstance(model, dict):
        return None, None

    return (
        str(model.get("default") or "") or None,
        str(model.get("provider") or "") or None,
    )


def current_review(config: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    auxiliary = config.get("auxiliary")

    if not isinstance(auxiliary, dict):
        return None, None

    review = auxiliary.get("review")

    if not isinstance(review, dict):
        return None, None

    return (
        str(review.get("model") or "") or None,
        str(review.get("provider") or "") or None,
    )


def current_fallbacks(
    config: Dict[str, Any],
) -> List[Tuple[str, str]]:

    raw = config.get("fallback_providers")

    if not isinstance(raw, list):
        return []

    result = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        provider = str(item.get("provider") or "")
        model = str(item.get("model") or "")

        if provider and model:
            result.append((provider, model))

    return result


def find_current(
    models: List[Dict[str, Any]],
    model_id_value: Optional[str],
    provider: Optional[str],
) -> Optional[Dict[str, Any]]:

    if not model_id_value:
        return None

    # Primeiro tenta provider + ID.
    for model in models:
        if (
            model["_model_id"] == model_id_value
            and (
                not provider
                or model["_provider"] == provider
                or model["_source"] == provider
            )
        ):
            return model

    # Depois só ID.
    for model in models:
        if model["_model_id"] == model_id_value:
            return model

    return None


def should_switch(
    current: Optional[Dict[str, Any]],
    candidate: Dict[str, Any],
    role: str,
    force: bool,
) -> bool:

    if force or current is None:
        return True

    # Mesma família/mesma rota: não mexe.
    if (
        current["_family"] == candidate["_family"]
        and current["_model_id"] == candidate["_model_id"]
    ):
        return False

    current_score = score_role(current, role)
    candidate_score = score_role(candidate, role)

    return candidate_score >= current_score + MIN_IMPROVEMENT


# =============================================================================
# CONFIG WRITING
# =============================================================================

# Endpoints reais usados no config.yaml do Hermes.
# nous e cloudflare roteiam como provider "custom" + key_env (o provider nativo
# "nous" exige OAuth device-code; "custom" + Bearer key_env contorna).
# O account id vem do .env (carregado em run(), DEPOIS do import) — por isso a
# base_url da Cloudflare é resolvida na hora de usar, nunca como constante.
CLOUDFLARE_BASE_URL_TMPL = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1"

PROVIDER_ENDPOINTS = {
    "openrouter": {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": None,
    },
    "nvidia": {
        "provider": "nvidia",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_env": None,
    },
    "nous": {
        "provider": "custom",
        "base_url": NOUS_BASE_URL,
        "key_env": "NOUS_API_KEY",
    },
    "cloudflare": {
        "provider": "custom",
        "base_url": CLOUDFLARE_BASE_URL_TMPL,
        "key_env": "CLOUDFLARE_API_TOKEN",
    },
    # Provider nativo do Hermes (GeminiNativeClient). key_env fixa a chave válida:
    # sem ela o pool do Hermes pode rotacionar para um GEMINI_API_KEY placeholder.
    "google": {
        "provider": "gemini",
        "base_url": GOOGLE_API_BASE,
        "key_env": "GOOGLE_API_KEY",
    },
    # OAuth gerenciado pelo Hermes (auth.json): sem key_env.
    "openai-codex": {
        "provider": "openai-codex",
        "base_url": CODEX_BASE_URL,
        "key_env": None,
    },
}


def provider_for_config(model: Dict[str, Any]) -> str:
    source = model["_source"]

    endpoint = PROVIDER_ENDPOINTS.get(source)
    if endpoint is None:
        raise RuntimeError(
            f"Provider sem configuração conhecida: {source}"
        )

    return endpoint["provider"]


def config_entry(model: Dict[str, Any]) -> Dict[str, Any]:
    """Monta a entrada provider/model/base_url/[key_env] no formato exato que o
    Hermes carrega hoje (igual às entradas já funcionando em fallback_providers)."""
    source = model["_source"]

    endpoint = PROVIDER_ENDPOINTS.get(source)
    if endpoint is None:
        raise RuntimeError(
            f"Provider sem configuração conhecida: {source}"
        )

    entry: Dict[str, Any] = {
        "provider": endpoint["provider"],
        "model": model["_model_id"],
        "base_url": endpoint["base_url"].format(
            account=os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        ),
    }

    if endpoint["key_env"]:
        entry["key_env"] = (google_key_env() if source == "google"
                            else endpoint["key_env"])

    return entry


def apply_config(
    original: Dict[str, Any],
    main: Dict[str, Any],
    moa: Optional[Dict[str, Any]],
    fallbacks: List[Dict[str, Any]],
    moa_refs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Grava a seleção nos caminhos REAIS do config.yaml do Hermes:

      - model.default / model.provider / model.base_url
      - moa.aggregator + moa.presets.default.aggregator  (MOA/REVIEW)
      - moa.reference_models + moa.presets.default.reference_models
        (só se moa_refs não-vazio; senão preserva o que existe)
      - auxiliary.moa_aggregator + auxiliary.moa_reference
      - fallback_providers  (cadeia completa, SEM repetir o MAIN)

    O MAIN é excluído de fallback_providers: fallback é rede de segurança por-turno;
    não faz sentido o MAIN aparecer de novo logo abaixo de si mesmo.
    """
    config = copy.deepcopy(original)

    main_entry = config_entry(main)
    main_family = main["_family"]

    # ---- model.default (MAIN) ----
    model_cfg = config.setdefault("model", {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        config["model"] = model_cfg

    model_cfg["default"] = main_entry["model"]
    model_cfg["provider"] = main_entry["provider"]
    model_cfg["base_url"] = main_entry["base_url"]

    # ---- MOA / REVIEW ----
    # Agregador MoA no formato do config real (provider/model/base_url[/key_env]).
    if moa:
        agg = config_entry(moa)
    else:
        agg = main_entry  # fallback defensivo: MoA cai no MAIN se não houver MOA

    moa_cfg = config.setdefault("moa", {})
    if not isinstance(moa_cfg, dict):
        moa_cfg = {}
        config["moa"] = moa_cfg

    moa_cfg["aggregator"] = copy.deepcopy(agg)

    # Reference models: formato {provider, model, base_url, [key_env], enabled}.
    ref_cfg: Optional[List[Dict[str, Any]]] = None
    if moa_refs:
        ref_cfg = []
        for ref in moa_refs:
            e = config_entry(ref)
            e["enabled"] = True
            ref_cfg.append(e)
        moa_cfg["reference_models"] = copy.deepcopy(ref_cfg)

    presets = moa_cfg.setdefault("presets", {})
    if isinstance(presets, dict):
        default_preset = presets.setdefault("default", {})
        if isinstance(default_preset, dict):
            default_preset["aggregator"] = copy.deepcopy(agg)
            if ref_cfg is not None:
                default_preset["reference_models"] = copy.deepcopy(ref_cfg)

    # ---- auxiliary.moa_aggregator / auxiliary.moa_reference ----
    auxiliary = config.setdefault("auxiliary", {})
    if not isinstance(auxiliary, dict):
        auxiliary = {}
        config["auxiliary"] = auxiliary

    for aux_key in ("moa_aggregator", "moa_reference"):
        aux_entry = auxiliary.setdefault(aux_key, {})
        if not isinstance(aux_entry, dict):
            aux_entry = {}
            auxiliary[aux_key] = aux_entry

        aux_entry["provider"] = agg["provider"]
        aux_entry["model"] = agg["model"]
        aux_entry["base_url"] = agg["base_url"]
        if agg.get("key_env"):
            # mantém api_key vazio; a credencial vem do key_env no provider
            aux_entry["api_key"] = ""

    # ---- fallback_providers (completos, sem repetir o MAIN) ----
    fallback_cfg: List[Dict[str, Any]] = []
    seen_families = {main_family}

    for item in fallbacks:
        if item["_family"] in seen_families:
            continue
        fallback_cfg.append(config_entry(item))
        seen_families.add(item["_family"])

    config["fallback_providers"] = fallback_cfg

    return config


def atomic_write_yaml(
    path: Path,
    data: Dict[str, Any],
) -> None:

    content = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )

    fd, tmp_name = tempfile.mkstemp(
        prefix=".config.yaml.",
        dir=str(path.parent),
        text=True,
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(tmp_name, path)

    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def backup_config() -> Path:
    timestamp = dt.datetime.now().strftime(
        "%Y%m%d-%H%M%S"
    )

    backup = CONFIG_FILE.with_name(
        f"{CONFIG_FILE.name}.bak.{timestamp}"
    )

    shutil.copy2(CONFIG_FILE, backup)

    return backup


# =============================================================================
# OUTPUT
# =============================================================================

def print_model(
    label: str,
    model: Optional[Dict[str, Any]],
) -> None:

    if not model:
        print(f"{label}: —")
        return

    caps = model["_caps"]

    print(f"{label}:")
    print(f"  source:    {model['_source']}")
    print(f"  provider:  {provider_for_config(model)}")
    print(f"  model:     {model['_model_id']}")
    print(f"  family:    {model['_family']}")
    print(f"  context:   {model['_context']:,}")

    print(
        "  caps:      "
        f"coding={caps['coding']:.0f} "
        f"reasoning={caps['reasoning']:.0f} "
        f"agent={caps['agentic']:.0f} "
        f"tools={caps['tools']:.0f} "
        f"long={caps['long_context']:.0f} "
        f"general={caps['general']:.0f}"
    )

    if "_fallback_label" in model:
        print(
            f"  função:    {model['_fallback_label']}"
        )


def validate_final_selection(
    main: Dict[str, Any],
    moa: Optional[Dict[str, Any]],
    fallbacks: List[Dict[str, Any]],
) -> None:

    selected = [main]

    if moa:
        selected.append(moa)

    selected.extend(fallbacks)

    families = [
        item["_family"]
        for item in selected
    ]

    if len(families) != len(set(families)):
        raise RuntimeError(
            "VALIDAÇÃO FALHOU: mesma família de LLM aparece "
            "em mais de uma posição."
        )

    if len(selected) > 1:
        # Não é obrigatório ter providers diferentes em todas as posições,
        # mas, se houver alternativas, a seleção tenta distribuí-los.
        sources = [
            item["_source"]
            for item in selected
        ]

        logging.info(
            "Diversidade de fontes: %s",
            ", ".join(sources),
        )


def print_selection(
    title: str,
    main: Dict[str, Any],
    moa: Optional[Dict[str, Any]],
    fallbacks: List[Dict[str, Any]],
) -> None:

    print("\n" + "=" * 84)
    print(title)
    print("=" * 84)

    print_model("MAIN", main)
    print()
    print_model("MOA / REVIEW", moa)

    for index, fallback in enumerate(
        fallbacks,
        start=1,
    ):
        print()
        print_model(
            f"FALLBACK {index}",
            fallback,
        )

    print("\n" + "-" * 84)
    print("REGRA: nenhuma família de LLM pode se repetir.")
    print("REGRA: fallbacks possuem perfis funcionais diferentes.")
    print("-" * 84)


# =============================================================================
# STATE / HISTORY
# =============================================================================

def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        logging.warning(
            "state.json inválido; iniciando novo estado."
        )
        return {}


def save_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def append_history(entry: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    with HISTORY_FILE.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                entry,
                ensure_ascii=False,
            )
            + "\n"
        )


def serialize_model(
    model: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:

    if not model:
        return None

    return {
        "source": model["_source"],
        "provider": provider_for_config(model),
        "model": model["_model_id"],
        "family": model["_family"],
        "context": model["_context"],
        "capabilities": model["_caps"],
        "fallback_profile": model.get("_fallback_profile"),
        "fallback_label": model.get("_fallback_label"),
    }


# =============================================================================
# MAIN
# =============================================================================

def run(args: argparse.Namespace) -> int:
    setup_logging(args.verbose)
    load_dotenv(ENV_FILE)

    logging.info(
        "=== Hermes Free Multi-Provider Selector ==="
    )

    # Carrega o tracker de confiabilidade (persistente): latência EMA + falhas
    # consecutivas por modelo, acumulados em execuções anteriores. Alimenta o
    # score (penalidade) e a quarentena. _REL é global p/ score_role consultar.
    global _REL
    _REL = load_reliability()

    models = collect_models()

    if not models:
        raise RuntimeError(
            "Nenhuma LLM gratuita foi encontrada nas quatro fontes."
        )

    # Remove do pool os modelos em QUARENTENA (falharam REL_DEAD_FAILS vezes
    # seguidas). Não adianta manter na lista quem vive dando timeout.
    not_free = _load_not_free()
    if not_free:
        nf = [m for m in models if m["_model_id"] in not_free]
        models = [m for m in models if m["_model_id"] not in not_free]
        logging.info("Não-free (plano do provedor): %d excluídos: %s", len(nf),
                     ", ".join(sorted({m["_model_id"] for m in nf}))[:400])
    before = len(models)
    quarantined = [m for m in models if is_quarantined(_REL, m)]
    models = [m for m in models if not is_quarantined(_REL, m)]
    if quarantined:
        logging.info(
            "Quarentena: %d/%d modelos pulados por falhas consecutivas: %s",
            len(quarantined), before,
            ", ".join(sorted({m["_model_id"] for m in quarantined}))[:400],
        )

    if not models:
        raise RuntimeError(
            "Todos os modelos elegíveis estão em quarentena por falhas repetidas."
        )

    # P1: grava catálogo auditável ANTES de qualquer seleção/alteração.
    # Anota benchmarks da Artificial Analysis (só nos modelos FREE já filtrados).
    global _AA_STATUS
    _AA_STATUS = aa_scores.annotate(models, STATE_DIR)
    logging.info(
        "Artificial Analysis: %s | origem=%s | %d/%d modelos free com benchmark",
        "ATIVO" if _AA_STATUS["used"] else "INATIVO (heurística)",
        _AA_STATUS["origin"], _AA_STATUS["matched"], _AA_STATUS["total"],
    )
    write_catalog(models)

    # -------------------------------------------------------------------------
    # PRIMEIRA SELEÇÃO
    # -------------------------------------------------------------------------

    main_candidate = choose_main(models)

    if not main_candidate:
        raise RuntimeError(
            "Não foi possível encontrar MAIN."
        )

    used_families = {
        main_candidate["_family"]
    }

    used_sources = {
        main_candidate["_source"]
    }

    moa_candidate = choose_moa(
        models,
        used_families,
        used_sources,
    )

    if moa_candidate:
        used_families.add(
            moa_candidate["_family"]
        )
        used_sources.add(
            moa_candidate["_source"]
        )

    fallback_candidates = choose_fallbacks(
        models,
        used_families,
        used_sources,
    )

    validate_final_selection(
        main_candidate,
        moa_candidate,
        fallback_candidates,
    )

    print_selection(
        "CANDIDATOS CALCULADOS",
        main_candidate,
        moa_candidate,
        fallback_candidates,
    )

    # -------------------------------------------------------------------------
    # CONFIG ATUAL
    # -------------------------------------------------------------------------

    config = load_config()

    current_main_id, current_main_provider = current_main(config)
    current_review_id, current_review_provider = current_review(config)

    current_main_model = find_current(
        models,
        current_main_id,
        current_main_provider,
    )

    current_review_model = find_current(
        models,
        current_review_id,
        current_review_provider,
    )

    # -------------------------------------------------------------------------
    # POLÍTICA DE TROCA MAIN
    # -------------------------------------------------------------------------

    if should_switch(
        current_main_model,
        main_candidate,
        "main",
        args.force,
    ):
        final_main = main_candidate
        logging.info(
            "MAIN: selecionado novo candidato."
        )
    else:
        final_main = current_main_model
        logging.info(
            "MAIN: mantendo modelo atual."
        )

    if final_main is None:
        final_main = main_candidate

    # -------------------------------------------------------------------------
    # MOA — precisa continuar diferente do MAIN
    # -------------------------------------------------------------------------

    if current_review_model:
        current_review_conflicts = (
            current_review_model["_family"]
            == final_main["_family"]
        )
    else:
        current_review_conflicts = False

    if (
        current_review_model
        and not current_review_conflicts
        and not should_switch(
            current_review_model,
            moa_candidate,
            "moa",
            args.force,
        )
    ):
        final_moa = current_review_model
        logging.info(
            "MOA: mantendo modelo atual."
        )
    else:
        final_moa = moa_candidate
        logging.info(
            "MOA: selecionado novo candidato."
        )

    # Se MAIN preservado mudou a combinação de famílias, recalcula.
    final_used_families = {
        final_main["_family"]
    }

    final_used_sources = {
        final_main["_source"]
    }

    if final_moa:
        if final_moa["_family"] in final_used_families:
            final_moa = choose_moa(
                models,
                final_used_families,
                final_used_sources,
            )

        if final_moa:
            final_used_families.add(
                final_moa["_family"]
            )
            final_used_sources.add(
                final_moa["_source"]
            )

    # -------------------------------------------------------------------------
    # FALLBACKS — sempre recalculados para manter complementaridade
    # -------------------------------------------------------------------------

    final_fallbacks = choose_fallbacks(
        models,
        final_used_families,
        final_used_sources,
    )

    # -------------------------------------------------------------------------
    # VALIDAÇÃO POR PROBE ANTES DE GRAVAR  (arquitetura: validar Free real)
    # Catálogo expõe metadados, não garante que o endpoint responde. Aqui cada
    # posição é sondada; se falhar (404/403/etc), a família entra na dead set e
    # a posição é re-selecionada. Evita gravar MAIN/MOA quebrado no config.
    # -------------------------------------------------------------------------

    dead_families: Set[str] = set()

    if not args.skip_probe:
        # MAIN precisa responder; tenta re-selecionar até achar um vivo.
        for _ in range(8):
            if probe_ok(final_main):
                break
            logging.warning(
                "MAIN %s falhou no probe; re-selecionando.",
                final_main["_model_id"],
            )
            dead_families.add(final_main["_family"])
            cand = choose_main(
                [m for m in models if m["_family"] not in dead_families]
            )
            if not cand:
                raise RuntimeError(
                    "Nenhum MAIN válido respondeu ao probe."
                )
            final_main = cand

        # MOA precisa responder e ser != MAIN.
        used = {final_main["_family"]}
        used_src = {final_main["_source"]}
        for _ in range(8):
            if final_moa and final_moa["_family"] not in used and probe_ok(final_moa):
                break
            if final_moa:
                logging.warning(
                    "MOA %s falhou no probe/conflito; re-selecionando.",
                    final_moa["_model_id"],
                )
                dead_families.add(final_moa["_family"])
            final_moa = choose_moa(
                [m for m in models if m["_family"] not in dead_families],
                used,
                used_src,
            )
            if not final_moa:
                logging.warning("Sem MOA válido; seguindo sem MoA dedicado.")
                break

        # Recompõe famílias/fontes usadas e revalida os fallbacks um a um.
        final_used_families = {final_main["_family"]}
        final_used_sources = {final_main["_source"]}
        if final_moa:
            final_used_families.add(final_moa["_family"])
            final_used_sources.add(final_moa["_source"])

        validated_fallbacks: List[Dict[str, Any]] = []
        for fb in final_fallbacks:
            if fb["_family"] in dead_families:
                continue
            # pula colisão com MAIN/MOA (re-seleção pode ter puxado uma família
            # que já está num fallback existente).
            if fb["_family"] in final_used_families:
                continue
            if probe_ok(fb):
                validated_fallbacks.append(fb)
                final_used_families.add(fb["_family"])
            else:
                logging.warning(
                    "FALLBACK %s falhou no probe; descartado.",
                    fb["_model_id"],
                )
                dead_families.add(fb["_family"])

        # Se perdeu fallbacks, tenta completar com novos candidatos vivos.
        if len(validated_fallbacks) < FALLBACK_COUNT:
            used_fam = set(final_used_families) | {
                f["_family"] for f in validated_fallbacks
            }
            used_src = set(final_used_sources) | {
                f["_source"] for f in validated_fallbacks
            }
            extra = choose_fallbacks(
                [m for m in models if m["_family"] not in dead_families],
                used_fam,
                used_src,
            )
            for fb in extra:
                if len(validated_fallbacks) >= FALLBACK_COUNT:
                    break
                if fb["_family"] in used_fam:
                    continue
                if probe_ok(fb):
                    validated_fallbacks.append(fb)
                    used_fam.add(fb["_family"])

        final_fallbacks = validated_fallbacks

    # -------------------------------------------------------------------------
    # MOA REFERENCE MODELS — v4 é o único escritor (substitui update_models.py).
    # Famílias distintas de MAIN/aggregator; não excluímos famílias dos
    # fallbacks (papéis distintos; o pool free não comporta exclusão total).
    # -------------------------------------------------------------------------
    final_moa_refs: List[Dict[str, Any]] = []
    if MOA_REFERENCE_COUNT > 0:
        ref_dead: Set[str] = set(dead_families)
        ref_used_fam = {final_main["_family"]}
        ref_used_src = {final_main["_source"]}
        if final_moa:
            ref_used_fam.add(final_moa["_family"])
            ref_used_src.add(final_moa["_source"])
        for _ in range(6):
            need = MOA_REFERENCE_COUNT - len(final_moa_refs)
            if need <= 0:
                break
            pool = [m for m in models if m["_family"] not in ref_dead]
            cands = choose_moa_references(
                pool,
                ref_used_fam | {r["_family"] for r in final_moa_refs},
                ref_used_src | {r["_source"] for r in final_moa_refs},
                count=need,
                used_vendors={_vendor(final_main)}
                | ({_vendor(final_moa)} if final_moa else set())
                | {_vendor(r) for r in final_moa_refs},
            )
            if not cands:
                break
            for ref in cands:
                if args.skip_probe or probe_ok(ref):
                    final_moa_refs.append(ref)
                else:
                    logging.warning(
                        "MOA_REF %s falhou no probe; re-selecionando.",
                        ref["_model_id"],
                    )
                    ref_dead.add(ref["_family"])
        if len(final_moa_refs) < MOA_REFERENCE_COUNT:
            logging.warning(
                "MoA: só %d/%d reference models válidos.",
                len(final_moa_refs), MOA_REFERENCE_COUNT,
            )
        for i, ref in enumerate(final_moa_refs, 1):
            logging.info(
                "MOA_REF%d: %s (%s) score=%.1f",
                i, ref["_model_id"], ref["_source"], score_role(ref, "moa"),
            )

    validate_final_selection(
        final_main,
        final_moa,
        final_fallbacks,
    )

    print_selection(
        "SELEÇÃO FINAL",
        final_main,
        final_moa,
        final_fallbacks,
    )

    # -------------------------------------------------------------------------
    # DRY RUN
    # -------------------------------------------------------------------------

    if args.dry_run:
        logging.info(
            "DRY-RUN: config.yaml não será alterado."
        )
    else:
        new_config = apply_config(
            config,
            final_main,
            final_moa,
            final_fallbacks,
            final_moa_refs,
        )

        # Validação YAML antes de qualquer alteração.
        yaml.safe_dump(
            new_config,
            allow_unicode=True,
            sort_keys=False,
        )

        backup = backup_config()

        try:
            atomic_write_yaml(
                CONFIG_FILE,
                new_config,
            )
        except Exception:
            logging.exception(
                "Falha na gravação; restaurando backup."
            )
            shutil.copy2(
                backup,
                CONFIG_FILE,
            )
            raise

        logging.info(
            "config.yaml atualizado."
        )
        logging.info(
            "Backup criado: %s",
            backup,
        )

    # -------------------------------------------------------------------------
    # P4 — RESTART + WARM-UP (apenas fora de dry-run)
    # -------------------------------------------------------------------------

    warmup_results: List[Dict[str, Any]] = []
    gateway_active: Optional[bool] = None

    if not args.dry_run and not args.no_restart and not _can_restart_gateway():
        # Windows/macOS/sem systemd: o gateway relê config.yaml a cada mensagem;
        # sem restart só as sessões já abertas mantêm o modelo anterior.
        logging.info(
            "Sem systemctl --user (ou HERMES_GATEWAY_RESTART=0): restart pulado; "
            "o gateway relê config.yaml a cada mensagem. Opcional: hermes gateway restart."
        )
        warmup_results = run_warmups(final_main, final_moa, final_fallbacks)
    elif not args.dry_run and not args.no_restart:
        unit = gateway_unit_name()
        if args.cron_mode:
            # Modo cron: roda DENTRO do gateway. Warm-up ANTES (testa endpoints
            # direto), depois restart --no-block (não espera active p/ não se matar).
            warmup_results = run_warmups(final_main, final_moa, final_fallbacks)
            gateway_active = restart_gateway(unit, no_block=True)
        else:
            gateway_active = restart_gateway(unit)
            if gateway_active:
                logging.info("Gateway ativo — iniciando warm-up dos modelos.")
            else:
                logging.warning(
                    "Gateway não confirmou 'active'; warm-up roda mesmo assim "
                    "(testa os endpoints diretamente)."
                )
            warmup_results = run_warmups(final_main, final_moa, final_fallbacks)
    elif not args.dry_run and args.no_restart:
        logging.info("--no-restart: config gravado, gateway NÃO reiniciado.")
        warmup_results = run_warmups(final_main, final_moa, final_fallbacks)

    # -------------------------------------------------------------------------
    # HISTORY
    # -------------------------------------------------------------------------

    timestamp = dt.datetime.now(
        dt.timezone.utc
    ).isoformat()

    entry = {
        "timestamp": timestamp,
        "dry_run": args.dry_run,
        "sources_seen": sorted(
            {
                model["_source"]
                for model in models
            }
        ),
        "eligible_count": len(models),
        "gateway_active": gateway_active,
        "main": serialize_model(final_main),
        "moa": serialize_model(final_moa),
        "moa_references": [serialize_model(r) for r in final_moa_refs],
        "fallbacks": [
            serialize_model(item)
            for item in final_fallbacks
        ],
        "warmup": warmup_results,
    }

    append_history(entry)

    state = {
        "updated_at": timestamp,
        "gateway_active": gateway_active,
        "main": serialize_model(final_main),
        "moa": serialize_model(final_moa),
        "moa_references": [serialize_model(r) for r in final_moa_refs],
        "fallbacks": [
            serialize_model(item)
            for item in final_fallbacks
        ],
        "warmup": warmup_results,
    }

    save_state(state)

    # Persiste o tracker de confiabilidade (latência EMA + falhas) para a próxima
    # execução penalizar/quarentenar quem andou lento ou falhando.
    save_reliability(_REL)

    logging.info(
        "Estado salvo em %s",
        STATE_FILE,
    )

    logging.info(
        "Histórico salvo em %s",
        HISTORY_FILE,
    )

    return 0


# =============================================================================
# P4 — RESTART DO GATEWAY + WARM-UP
# =============================================================================

def _can_restart_gateway() -> bool:
    """Restart via systemd só em Linux com systemctl no PATH e sem opt-out."""
    if os.environ.get("HERMES_GATEWAY_RESTART", "1") == "0":
        return False
    return sys.platform.startswith("linux") and shutil.which("systemctl") is not None


def gateway_unit_name() -> str:
    """Serviço systemd do gateway que SERVE este perfil.

    Ordem: env HERMES_GATEWAY_UNIT > hermes-gateway-<perfil>.service se ATIVO >
    hermes-gateway.service (host multiplex, serve todos os perfis). Nunca reinicia
    um unit standalone inativo/desabilitado (Restart=always + exit 75 = loop).
    """
    env_unit = os.environ.get("HERMES_GATEWAY_UNIT")
    if env_unit:
        return env_unit

    # Se um gateway standalone do perfil estiver ATIVO, use-o; senão, o host.
    if HERMES_HOME.parent.name == "profiles":
        standalone = f"hermes-gateway-{HERMES_HOME.name}.service"
        try:
            chk = subprocess.run(
                ["systemctl", "--user", "is-active", standalone],
                capture_output=True, text=True, timeout=10,
            )
            if (chk.stdout or "").strip() == "active":
                return standalone
        except (OSError, subprocess.TimeoutExpired):
            pass

    # Host gateway multiplex (serve todos os perfis).
    return "hermes-gateway.service"


def restart_gateway(unit: str, wait_active_s: int = 60, no_block: bool = False) -> bool:
    """Reinicia o gateway e espera ficar 'active'. Retorna True se ativo.

    no_block=True (modo cron): usa systemctl --no-block e NÃO espera 'active'.
    Necessário quando o chamador roda DENTRO do próprio gateway (o cron ticker):
    esperar o active mataria o processo que está executando este script.
    """
    logging.info("Reiniciando %s %s...", unit, "(--no-block) " if no_block else "")
    cmd = ["systemctl", "--user"]
    if no_block:
        cmd.append("--no-block")
    cmd += ["restart", unit]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=wait_active_s,
        )
        if r.returncode != 0:
            logging.error(
                "Falha ao reiniciar %s: rc=%s %s",
                unit, r.returncode, (r.stderr or "").strip()[:300],
            )
            return False
    except (OSError, subprocess.TimeoutExpired) as exc:
        logging.error("Erro no restart de %s: %r", unit, exc)
        return False

    if no_block:
        logging.info("Restart de %s enfileirado (--no-block).", unit)
        return True

    # Espera o estado active (poll a cada 2s).
    deadline = time.monotonic() + wait_active_s
    while time.monotonic() < deadline:
        try:
            chk = subprocess.run(
                ["systemctl", "--user", "is-active", unit],
                capture_output=True, text=True, timeout=10,
            )
            state = (chk.stdout or "").strip()
            if state == "active":
                logging.info("%s está active.", unit)
                return True
            if state in ("failed", "inactive"):
                logging.error("%s entrou em estado '%s'.", unit, state)
                return False
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(2)

    logging.warning("%s não ficou active dentro de %ss.", unit, wait_active_s)
    return False


def _resolve_key(model: Dict[str, Any]) -> Optional[str]:
    """Chave de API para o warm-up conforme a fonte do modelo."""
    source = model["_source"]
    if source == "openrouter":
        return env_first("OPENROUTER_API_KEY")
    if source == "nvidia":
        return env_first("NVIDIA_API_KEY")
    if source == "nous":
        return env_first("NOUS_API_KEY", "NOUS_PORTAL_API_KEY", "NOUS_TOKEN")
    if source == "cloudflare":
        return env_first("CLOUDFLARE_API_TOKEN")
    if source == "google":
        return google_key()
    if source == "openai-codex":
        cred = _codex_token()
        return cred[0] if cred else None
    return None


# Google free tier = ~20 req/dia por modelo: o warm-up reaproveita o probe da
# mesma execução em vez de gastar outra requisição da cota.
_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}


def warmup_model(
    label: str,
    model: Optional[Dict[str, Any]],
    timeout_s: int = 40,
) -> Dict[str, Any]:
    """Faz um ping real /chat/completions e mede latência. Não falha o script:
    registra status + ms para auditoria."""
    if not model:
        return {"label": label, "ok": False, "reason": "sem modelo"}

    entry = config_entry(model)
    key = _resolve_key(model)
    result: Dict[str, Any] = {
        "label": label,
        "model": entry["model"],
        "provider": entry["provider"],
        "source": model["_source"],
    }

    if not key:
        result.update(ok=False, reason="sem credencial")
        logging.warning("Warm-up %s (%s): sem credencial.", label, entry["model"])
        return result

    if model["_source"] == "openai-codex":
        # Responses API + cota mensal minúscula: não gasta requisição de chat.
        # A cota foi conferida no /wham/usage durante a coleta do catálogo.
        result.update(ok=True, http_status=200, latency_ms=None, reason="cota ok via /wham/usage")
        logging.info("%s (%s): sem ping — cota conferida via /wham/usage (%s%% usada).",
                     label, entry["model"], _CODEX_STATUS.get("used_percent"))
        return result

    if model["_source"] == "google" and model["_model_id"] in _PROBE_CACHE:
        cached = dict(_PROBE_CACHE[model["_model_id"]], label=label, cached=True)
        logging.info("%s (%s): reaproveita probe (HTTP %s), poupa cota Google.",
                     label, entry["model"], cached.get("http_status"))
        return cached

    base = GOOGLE_OPENAI_BASE if model["_source"] == "google" else entry["base_url"]
    url = base.rstrip("/") + "/chat/completions"
    body = json.dumps({
        "model": entry["model"],
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 8,
    }).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Hermes-Free-MultiProvider-Selector/4.0",
        },
        method="POST",
    )

    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            _ = resp.read(256)
            status = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code  # 429 = existe/no ar, só limitado
        try:
            err_body = exc.read(2048).decode("utf-8", "replace")
        except Exception:
            err_body = ""
        # Cloudflare: 403 code 5035 "not available on the Workers Free plan" =
        # modelo PAGO para esta conta -> exclui do pool de forma persistente.
        if "Workers Free plan" in err_body or '"code":5035' in err_body:
            mark_not_free(model, "cloudflare: indisponível no Workers Free plan")
        # Google: 429 "limit: 0" = modelo fora do free tier desta chave (pago).
        # 429 com limite > 0 = cota diária gasta: não seleciona agora, re-testa
        # na próxima execução (a cota zera todo dia).
        if model["_source"] == "google" and status == 429:
            if "limit: 0" in err_body or '"limit": 0' in err_body:
                mark_not_free(model, "google: fora do free tier (limit 0)")
            result["quota_exhausted"] = True
    except Exception as exc:
        result.update(
            ok=False, reason=f"erro: {type(exc).__name__}",
            latency_ms=round((time.monotonic() - start) * 1000),
        )
        logging.warning("Warm-up %s (%s): %s", label, entry["model"], exc)
        return result

    latency_ms = round((time.monotonic() - start) * 1000)
    # 429 = no ar, só limitado — exceto Google, onde 429 é cota diária/plano.
    ok = status == 200 or (status == 429 and model["_source"] != "google")
    result.update(ok=ok, http_status=status, latency_ms=latency_ms)
    if model["_source"] == "google" and label == "probe":
        _PROBE_CACHE[model["_model_id"]] = dict(result)
    logging.info(
        "Warm-up %s (%s): HTTP %s em %dms %s",
        label, entry["model"], status, latency_ms, "OK" if ok else "FALHOU",
    )
    return result


def run_warmups(
    main: Dict[str, Any],
    moa: Optional[Dict[str, Any]],
    fallbacks: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Testa MAIN, MOA, F1..Fn e retorna os resultados para auditoria.
    Também alimenta o tracker de confiabilidade com latência/falha de cada um."""
    results: List[Dict[str, Any]] = []
    for label, m in [("MAIN", main), ("MOA", moa)]:
        r = warmup_model(label, m)
        if m:
            record_reliability(_REL, m, ok=bool(r.get("ok")), latency_ms=r.get("latency_ms"))
        results.append(r)
    for i, fb in enumerate(fallbacks, start=1):
        r = warmup_model(f"FALLBACK {i}", fb)
        record_reliability(_REL, fb, ok=bool(r.get("ok")), latency_ms=r.get("latency_ms"))
        results.append(r)
    return results


def probe_ok(model: Optional[Dict[str, Any]]) -> bool:
    """Probe real do endpoint ANTES de gravar: True se HTTP 200/429.
    (429 = existe e está no ar, só limitado.) Timeout curto: só interessa se vive.
    Grava latência/falha no tracker de confiabilidade (_REL) para penalizar/
    quarentenar nas PRÓXIMAS execuções quem vive dando timeout."""
    if not model:
        return False
    # Google: thinking model + free tier pode passar de 15s.
    res = warmup_model("probe", model, timeout_s=30 if model["_source"] == "google" else 15)
    ok = bool(res.get("ok"))
    # Cota diária gasta não é falha do modelo: não acumula rumo à quarentena.
    if not res.get("quota_exhausted"):
        record_reliability(_REL, model, ok=ok, latency_ms=res.get("latency_ms"))
    return ok


# =============================================================================
# CLI
# =============================================================================

def main() -> int:
    # Windows: console cp1252 quebra com acentos/emojis do log.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(
        description=(
            "Seleciona automaticamente LLMs gratuitas de "
            "OpenRouter, Nous, Cloudflare, NVIDIA e Google para Hermes."
        )
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Calcula e mostra a seleção sem alterar o config.yaml.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora HERMES_MIN_IMPROVEMENT ao trocar MAIN/MOA.",
    )

    parser.add_argument(
        "--no-restart",
        action="store_true",
        help="Grava o config mas NÃO reinicia o gateway (warm-up ainda roda).",
    )

    parser.add_argument(
        "--skip-probe",
        action="store_true",
        help="Pula a validação por probe antes de gravar (NÃO recomendado).",
    )

    parser.add_argument(
        "--cron-mode",
        action="store_true",
        help="Modo cron: warm-up antes do restart e restart --no-block "
             "(evita auto-suicídio quando roda dentro do gateway).",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Mostra logs detalhados.",
    )

    args = parser.parse_args()

    try:
        return run(args)
    except KeyboardInterrupt:
        print(
            "\nInterrompido.",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        logging.exception(
            "ERRO FATAL: %s",
            exc,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
