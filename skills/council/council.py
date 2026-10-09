# Hermes Council — Mixture-of-Agents dinâmico sobre um perfil Hermes
# Lê <perfil>/config.yaml a cada execução. Perfil = $HERMES_HOME, ou
# $COUNCIL_PROFILE (nome) sob o home padrão do Hermes:
#   Linux/macOS ~/.hermes · Windows %LOCALAPPDATA%\hermes
# O seletor free-LLM v4 atualiza model.default, moa.* e fallback_providers.
#
# Estágios (cópia minimal do llm-council, sem servidor/web):
#   1. Cada membro responde à query em paralelo
#   2. Cada membro ranqueia as respostas anonimizadas
#   3. Chairman sintetiza a resposta final

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import httpx
import yaml

def _hermes_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        return (Path(base) if base else Path.home() / "AppData" / "Local") / "hermes"
    return Path.home() / ".hermes"


def _profile_home() -> Path:
    if os.environ.get("HERMES_HOME", "").strip():
        return Path(os.path.expandvars(os.path.expanduser(os.environ["HERMES_HOME"])))
    name = os.environ.get("COUNCIL_PROFILE", "").strip()
    root = _hermes_root()
    return root / "profiles" / name if name and name != "default" else root


HERMES_HOME = _profile_home()
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
STATE_PATH = Path(os.environ.get("COUNCIL_STATE_DIR") or Path(__file__).resolve().parent / "state")
STATE_PATH.mkdir(parents=True, exist_ok=True)

# Timeout por membro no estágio 1 (um modelo lento não segura a rodada) e
# quantas vezes cada posição pode ser trocada por um reserva após falhar.
MEMBER_TIMEOUT_S = float(os.environ.get("COUNCIL_MEMBER_TIMEOUT", "120"))
MEMBER_SWAPS = int(os.environ.get("COUNCIL_MEMBER_SWAPS", "2"))


def _load_profile_env():
    """O perfil guarda chaves em <perfil>/.env.
    Carrega em os.environ sem sobrescrever valores já definidos."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_profile_env()


@dataclass
class Member:
    name: str           # alias curto (ex: "m1-kimi") — o que aparece anonimamente
    provider: str
    model: str
    base_url: str
    api_key: str
    api_mode: str = "chat_completions"
    persona: str | None = None  # nome do arquivo em personas/ (sem .md)


def _load_persona(name: str) -> str:
    """Carrega um persona .md do diretório personas/ (SKILL.md style)."""
    p = Path(__file__).parent / "personas" / f"{name}.md"
    if not p.exists():
        return ""
    text = p.read_text(encoding="utf-8")
    # remove frontmatter yaml
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            text = text[end + 3:].strip()
    return text


@dataclass
class StageOneResult:
    member: str
    model: str
    text: str
    elapsed_s: float
    error: Optional[str] = None


@dataclass
class StageTwoReview:
    reviewer: str
    reviewer_model: str
    ranking: list[str]   # labels tipo ["Response A","Response B",...] em ordem de preferência
    raw: str
    error: Optional[str] = None


@dataclass
class CouncilResult:
    query: str
    started_at: str
    stage1: list[StageOneResult]
    stage2: list[StageTwoReview]
    chairman_model: str
    final: str
    total_elapsed_s: float
    swaps: list = None  # trocas automáticas de membro: [{slot, from, to, reason}]


# ---------------------------------------------------------------- config ---
def _resolve_api_key(entry: dict) -> Optional[str]:
    """Resolve o api_key de um provider a partir de key_env ou env var padrão."""
    key_env = entry.get("key_env")
    if key_env and os.environ.get(key_env):
        return os.environ[key_env]

    provider = entry.get("provider", "")
    # provider custom sem key_env: infere pela base_url (Cloudflare ≠ Nous)
    base = str(entry.get("base_url") or "").lower()
    if provider == "custom":
        if "cloudflare.com" in base:
            return os.environ.get("CLOUDFLARE_API_TOKEN") or None
        if "nousresearch.com" in base:
            return os.environ.get("NOUS_API_KEY") or None
    # convenções do Hermes
    candidates = {
        "nvidia": ["NVIDIA_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
        "custom": ["NOUS_API_KEY", "CLOUDFLARE_API_TOKEN"],
        "openai": ["OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "google": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
        "gemini": ["GOOGLE_API_KEY", "GEMINI_API_KEY"],
    }.get(provider, [])
    for name in candidates:
        if os.environ.get(name):
            return os.environ[name]
    return None


# Providers que não falam /chat/completions com chave (council só usa essa API):
# openai-codex = login ChatGPT (OAuth + Responses API). Pulados; entra o próximo slot.
_UNSUPPORTED_PROVIDERS = {"openai-codex"}


def _chat_base_url(provider: str, base_url: str) -> str:
    """base_url usável em <base>/chat/completions. O Hermes grava o Gemini com a
    base nativa (.../v1beta); a rota OpenAI-compatível fica em .../v1beta/openai."""
    base = (base_url or "").rstrip("/")
    if provider in ("gemini", "google") and not base.endswith("/openai"):
        base = (base or "https://generativelanguage.googleapis.com/v1beta") + "/openai"
    return base


def _skip_unsupported(entry: dict) -> bool:
    if str(entry.get("provider") or "") in _UNSUPPORTED_PROVIDERS:
        print(f"[council] pulando {entry.get('model')} ({entry.get('provider')}): "
              "provider sem API chat/completions por chave", file=sys.stderr)
        return True
    return False


def load_council_members(top_n: int = 4, pool_path: str | None = None) -> tuple[list[Member], Member]:
    """Lê config.yaml e devolve (members, chairman).
    Chairman = moa.aggregator — o modelo que o seletor free-llm escolheu
    para o MoA (slot ranqueado por qualidade, família e provider distintos
    do MAIN). MAIN (model.default) vira membro para opinião cruzada; sem
    MoA configurado, MAIN assume o lugar de chairman.
    Members  = chairman + MAIN + moa.reference_models, completando com
               fallback_providers; dedupe por modelo; só slots com chave.

    Se pool_path for dado (JSON gerado por pool.py), usa essa pool verificada
    em vez do config — útil quando slots do config estão em rate limit.
    """
    if pool_path and Path(pool_path).exists():
        pool = json.loads(Path(pool_path).read_text(encoding="utf-8"))
        members: list[Member] = []
        for i, entry in enumerate(pool[:top_n]):
            api_key = os.environ.get(entry.get("api_key_env", ""), "")
            if not api_key:
                continue
            alias = f"m{len(members)+1}-{entry['model'].split('/')[-1][:20]}"
            members.append(Member(
                name=alias,
                provider=entry["source"],
                model=entry["model"],
                base_url=entry["base_url"].rstrip("/"),
                api_key=api_key,
                persona=entry.get("persona"),
            ))
        if len(members) < 2:
            raise RuntimeError(f"Pool externa tem menos de 2 membros válidos ({len(members)})")
        return members, members[0]

    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    model = cfg["model"]
    main_entry = {
        "provider": model["provider"],
        "model": model["default"],
        "base_url": model.get("base_url", ""),
        "api_mode": model.get("api_mode", "chat_completions"),
    }
    moa = cfg.get("moa") or {}
    preset = ((moa.get("presets") or {}).get(moa.get("default_preset") or "default") or {})
    aggregator = preset.get("aggregator") or moa.get("aggregator")
    refs = preset.get("reference_models") or moa.get("reference_models") or []
    refs = [r for r in refs if r.get("enabled", True)]
    fallbacks = cfg.get("fallback_providers", [])

    # Chairman = o modelo que o free-llm escolheu para o MoA (moa.aggregator).
    # O seletor v4 já ranqueia esse slot por qualidade + fabricante/provider
    # distintos do MAIN, então é a escolha natural para sintetizar o council.
    # MAIN vira membro (opinião cruzada). Sem MoA configurado, MAIN assume.
    candidates: list = []
    if aggregator:
        candidates.append(aggregator)
    candidates.append(main_entry)
    candidates += refs + fallbacks

    # 1 membro por provider: chamadas paralelas no mesmo provider estouram
    # rate limit (429). 1ª passada só providers inéditos; 2ª completa repetindo.
    members: list[Member] = []
    seen: set[str] = set()
    used_providers: set[str] = set()
    for allow_repeat in (False, True):
        for entry in candidates:
            if len(members) >= top_n:
                break
            mid = str(entry.get("model") or "")
            # dedupe por modelo (ignora :free) — o mesmo modelo em 2 slots não soma opinião
            key = mid.lower().removesuffix(":free")
            if not mid or key in seen or entry.get("provider") == "moa":
                continue
            if _skip_unsupported(entry):
                seen.add(key)
                continue
            pkey = provider_key(entry)
            if pkey in used_providers and not allow_repeat:
                continue
            api_key = _resolve_api_key(entry)
            base_url = _chat_base_url(str(entry.get("provider") or ""), entry.get("base_url") or "")
            if not api_key or not base_url:
                continue
            seen.add(key)
            used_providers.add(pkey)
            alias = f"m{len(members)+1}-{entry['model'].split('/')[-1][:20]}"
            members.append(Member(
                name=alias,
                provider=entry["provider"],
                model=entry["model"],
                base_url=base_url.rstrip("/"),
                api_key=api_key,
                api_mode=entry.get("api_mode", "chat_completions"),
            ))
        if len(members) >= top_n:
            break

    if len(members) < 2:
        raise RuntimeError(f"Council precisa de >=2 membros com chave; achei {len(members)}")
    chairman = members[0]  # moa.aggregator (MAIN se não houver MoA)
    return members, chairman


# ---------------------------------------------------------------- reserva ---
# Endpoints por fonte do catálogo do seletor free-llm (model-selector/catalog.json).
_SOURCE_ENDPOINTS = {
    "nvidia": ("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "openrouter": ("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "nous": ("custom", "https://inference-api.nousresearch.com/v1", "NOUS_API_KEY"),
    "cloudflare": ("custom", "https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1",
                   "CLOUDFLARE_API_TOKEN"),
    "google": ("gemini", "https://generativelanguage.googleapis.com/v1beta", "GOOGLE_API_KEY"),
}
# IDs que não são modelos de conversa (parsers, safety etc.)
_NOT_CHAT = ("parse", "guard", "safety", "embed", "rerank", "translate", "detector")


def provider_key(m: "Member | dict") -> str:
    """Provider real do membro/entrada: 'custom' é diferenciado pelo host."""
    from urllib.parse import urlparse
    prov = m.provider if isinstance(m, Member) else str(m.get("provider") or "")
    base = m.base_url if isinstance(m, Member) else str(m.get("base_url") or "")
    if prov == "custom":
        host = urlparse(base).hostname or ""
        return "custom:" + ".".join(host.split(".")[-2:])
    return prov


def _read_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load_reserves(exclude_models: set[str], limit: int = 12) -> list[Member]:
    """Reserva para substituir membros que falham (429/timeout/vazio).

    Fonte = avaliação automática do seletor free-llm: entradas do config.yaml
    ainda não usadas (MoA/fallbacks) e depois o catalog.json inteiro, por índice
    de inteligência da Artificial Analysis. Exclui quarentena (reliability.json,
    >=3 falhas seguidas) e recusados por plano (not_free.json). Sem catálogo
    (free-llm não instalado) a reserva usa só o config.
    """
    sel = HERMES_HOME / "model-selector"
    rel = _read_json(sel / "reliability.json") or {}
    not_free = _read_json(sel / "not_free.json") or {}
    catalog = _read_json(sel / "catalog.json")
    cat = catalog.get("models", []) if isinstance(catalog, dict) else []
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")

    entries: list[dict] = []
    try:
        cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
        moa = cfg.get("moa") or {}
        preset = (moa.get("presets") or {}).get(moa.get("default_preset") or "default") or {}
        entries += [preset.get("aggregator") or moa.get("aggregator") or {}]
        entries += preset.get("reference_models") or moa.get("reference_models") or []
        entries += cfg.get("fallback_providers") or []
    except (OSError, yaml.YAMLError):
        pass

    def score(c: dict) -> float:
        aa = c.get("aa") or {}
        if aa.get("intel") is not None:
            return 100 + float(aa["intel"])          # com benchmark vem antes
        return float((c.get("capabilities") or {}).get("reasoning") or 0)

    cat_entries: list[dict] = []
    for c in sorted(cat, key=score, reverse=True):
        src = c.get("source")
        if src not in _SOURCE_ENDPOINTS or c.get("model") in not_free:
            continue
        fails = int((rel.get(f"{src}::{c.get('family')}") or {}).get("fails", 0))
        if fails >= 3:
            continue
        if src == "cloudflare" and not account:
            continue
        prov, base, key_env = _SOURCE_ENDPOINTS[src]
        cat_entries.append({"provider": prov, "model": c["model"], "_fails": fails,
                            "base_url": base.format(account=account), "key_env": key_env})
    # falha recente vai para o fim da fila do catálogo (sort estável mantém a qualidade)
    cat_entries.sort(key=lambda e: 1 if e["_fails"] > 0 else 0)
    entries += cat_entries

    out: list[Member] = []
    seen = {m.lower().removesuffix(":free") for m in exclude_models}
    for e in entries:
        mid = str(e.get("model") or "")
        k = mid.lower().removesuffix(":free")
        if (not mid or k in seen or e.get("provider") == "moa" or any(t in k for t in _NOT_CHAT)
                or str(e.get("provider") or "") in _UNSUPPORTED_PROVIDERS):
            continue
        api_key = _resolve_api_key(e)
        if not api_key or not e.get("base_url"):
            continue
        seen.add(k)
        out.append(Member(name=f"r-{mid.split('/')[-1][:20]}", provider=e["provider"],
                          model=mid, base_url=_chat_base_url(str(e["provider"]), str(e["base_url"])),
                          api_key=api_key, api_mode=e.get("api_mode", "chat_completions")))
        if len(out) >= limit:
            break
    return out


def pick_reserve(reserves: list[Member], active_providers: "list[str] | set[str]",
                 failed_providers: set[str]) -> Optional[Member]:
    """Próximo reserva, sem provider que falhou nesta rodada: primeiro provider
    fora do council; senão o provider com MENOS membros ativos (espalha a carga
    em vez de empilhar no mesmo). Remove o escolhido da lista."""
    from collections import Counter
    load = Counter(active_providers)
    ok = [(i, r) for i, r in enumerate(reserves) if provider_key(r) not in failed_providers]
    if not ok:
        return None
    # ordem estável (prioridade da lista) desempatando pela carga do provider
    i, _ = min(ok, key=lambda t: (load.get(provider_key(t[1]), 0), t[0]))
    return reserves.pop(i)


# ---------------------------------------------------------------- client ---
async def _chat(client: httpx.AsyncClient, m: Member, messages: list[dict],
                temperature: float = 0.7, max_tokens: int = 8192,
                retries: int = 3) -> str:
    url = f"{m.base_url}/chat/completions"
    # persona (ponytail, etc.) vira system prompt
    if m.persona:
        pm = _load_persona(m.persona)
        if pm:
            messages = [{"role": "system", "content": pm}] + messages
    payload = {
        "model": m.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {m.api_key}"}
    last_err: Exception = RuntimeError("no attempt made")
    for attempt in range(retries):
        try:
            r = await client.post(url, json=payload, headers=headers, timeout=180)
            if r.status_code in (429, 500, 502, 503):
                raise httpx.HTTPStatusError(f"{r.status_code} rate limit/error", request=r.request, response=r)
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            text = msg.get("content")
            if not text:
                # alguns modelos (ex. kimi-k3 na NVIDIA) devolvem reasoning_content
                # e content=null quando truncados ou em modo raciocínio
                text = msg.get("reasoning_content") or ""
            if not text:
                raise RuntimeError(f"resposta vazia do modelo {m.model}")
            return text
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            last_err = e
            if attempt < retries - 1:
                await asyncio.sleep(5 * (attempt + 1))
    raise last_err


# ---------------------------------------------------------------- stages ---
async def stage_one(query: str, members: list[Member],
                    reserves: Optional[list[Member]] = None,
                    swaps: Optional[list[dict]] = None) -> list[StageOneResult]:
    """Cada membro responde a query independentemente, em paralelo.

    Membro que falha (429/timeout/vazio, após os retries) é trocado na hora por
    um reserva de outro provider — de preferência fora do council (rate limit é
    por provider) — até MEMBER_SWAPS trocas por posição. A lista ``members`` é
    atualizada in place para os estágios 2/3 usarem quem de fato respondeu.
    """
    reserves = reserves if reserves is not None else []
    swaps = swaps if swaps is not None else []
    failed_providers: set[str] = set()
    lock = asyncio.Lock()

    async with httpx.AsyncClient() as client:
        async def one(idx: int) -> StageOneResult:
            m = members[idx]
            for attempt in range(MEMBER_SWAPS + 1):
                t0 = time.time()
                try:
                    text = await asyncio.wait_for(
                        _chat(client, m, [{"role": "user", "content": query}]),
                        timeout=MEMBER_TIMEOUT_S)
                    return StageOneResult(m.name, m.model, text, time.time() - t0)
                except Exception as e:  # noqa: BLE001 — qualquer falha = trocar membro
                    err = f"{type(e).__name__}: {e}"[:200] if str(e) else type(e).__name__
                    async with lock:
                        failed_providers.add(provider_key(m))
                        active = [provider_key(x) for j, x in enumerate(members) if j != idx]
                        sub = pick_reserve(reserves, active, failed_providers) if attempt < MEMBER_SWAPS else None
                        if sub is not None:
                            sub.name = f"m{idx + 1}-{sub.model.split('/')[-1][:20]}"
                            sub.persona = m.persona
                            swaps.append({"slot": idx + 1, "from": m.model, "to": sub.model,
                                          "reason": err[:120]})
                            members[idx] = sub   # visível aos outros slots já na próxima escolha
                    if sub is None:
                        return StageOneResult(m.name, m.model, "", time.time() - t0, error=err)
                    m = sub
            return StageOneResult(m.name, m.model, "", 0.0, error="sem reserva")
        return await asyncio.gather(*[one(i) for i in range(len(members))])


def _anonymize(stage1: list[StageOneResult]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Devolve [(label, text)] e map label->member_name."""
    valid = [s for s in stage1 if not s.error and s.text]
    labels = [chr(ord("A") + i) for i in range(len(valid))]
    anon = [(f"Response {lab}", s.text) for lab, s in zip(labels, valid)]
    label_to_member = {f"Response {lab}": s.member for lab, s in zip(labels, valid)}
    return anon, label_to_member


def _parse_ranking(raw: str, valid_labels: list[str]) -> list[str]:
    """Extrai o ranking de forma tolerante: JSON array; senão, ordem em que os
    rótulos 'Response X' aparecem no texto (modelos de raciocínio costumam
    escrever prosa ou cortar o JSON). Só rótulos válidos, sem repetição."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.split("```")[1].lstrip("json").strip()
    start, end = s.find("["), s.rfind("]")
    if start >= 0 and end > start:
        try:
            arr = json.loads(s[start:end + 1])
            out = [x for x in arr if isinstance(x, str) and x in valid_labels]
            if len(out) >= 2:
                return list(dict.fromkeys(out))
        except ValueError:
            pass
    # fallback: última ocorrência de uma lista de rótulos no texto
    found = re.findall(r"Response\s+([A-Z])\b", s)
    out = list(dict.fromkeys(f"Response {x}" for x in found if f"Response {x}" in valid_labels))
    return out if len(out) >= 2 else []


async def stage_two(query: str, stage1: list[StageOneResult], members: list[Member]) -> tuple[list[StageTwoReview], dict[str, str]]:
    anon, label_to_member = _anonymize(stage1)
    if len(anon) < 2:
        return [], label_to_member
    valid_labels = [lab for lab, _ in anon]
    # Só quem respondeu no estágio 1 avalia (quem falhou tende a falhar de novo).
    answered = {s.member for s in stage1 if not s.error and s.text}
    reviewers = [m for m in members if m.name in answered] or members

    responses_block = "\n\n".join(f"{lab}:\n{txt}" for lab, txt in anon)
    prompt = (
        f"You are evaluating different responses to the following question.\n\n"
        f"Question: {query}\n\n"
        f"Here are the responses (from anonymous models):\n\n{responses_block}\n\n"
        f"Task: rank the responses from best to worst in accuracy and insight. "
        f"Output ONLY a JSON array of the labels in order, e.g. "
        f"[\"Response B\",\"Response A\",\"Response C\"]. No prose."
    )

    async with httpx.AsyncClient() as client:
        async def one(m: Member) -> StageTwoReview:
            try:
                raw = await asyncio.wait_for(
                    _chat(client, m, [{"role": "user", "content": prompt}],
                          temperature=0.0, max_tokens=2048),
                    timeout=MEMBER_TIMEOUT_S)
                ranking = _parse_ranking(raw, valid_labels)
                err = None if ranking else "ranking não reconhecido na resposta"
                return StageTwoReview(m.name, m.model, ranking, raw[:2000], error=err)
            except Exception as e:  # noqa: BLE001
                return StageTwoReview(m.name, m.model, [], "", error=f"{type(e).__name__}: {e}"[:200])
        reviews = await asyncio.gather(*[one(m) for m in reviewers])
        return reviews, label_to_member


CHAIRMAN_TEMPLATE = """You are the Chairman of an LLM Council. Several anonymous AI models answered a question, then ranked each other's answers.

Original question: {query}

Individual candidate responses:
{candidates}

Peer rankings (each reviewer listed responses best→worst; de-anonymized only after collection):
{rankings}

Your job: synthesize a single, final, best-quality answer for the user.
- Use the rankings as a signal of which responses are stronger.
- Merge the strongest accurate content; drop mutually-exclusive contradictions or call them out.
- Be direct. Portuguese (pt-BR) if the question was in pt-BR, otherwise follow the question's language.
- Do NOT mention that you are a chairman or that there was a council — just answer well.
"""


async def stage_three(query: str, stage1: list[StageOneResult],
                      stage2: list[StageTwoReview],
                      label_to_member: dict[str, str],
                      chairman: Member) -> str:
    candidates = "\n\n".join(
        f"--- Candidate {i+1} ({s.member}, model={s.model}) ---\n{s.text}"
        for i, s in enumerate(stage1) if not s.error
    )
    rank_lines = []
    for r in stage2:
        if r.error or not r.ranking:
            continue
        readable = [f"{lab}({label_to_member.get(lab,'?')})" for lab in r.ranking]
        rank_lines.append(f"- {r.reviewer}: " + " > ".join(readable))
    prompt = CHAIRMAN_TEMPLATE.format(
        query=query, candidates=candidates,
        rankings="\n".join(rank_lines) or "(no valid peer rankings)"
    )
    async with httpx.AsyncClient() as client:
        return await _chat(client, chairman, [{"role": "user", "content": prompt}],
                           temperature=0.5, max_tokens=16384)


def _is_coding_task(query: str) -> bool:
    """Heurística: a query parece uma tarefa de código?"""
    high = [
        r"\brefactor", r"\brefatore", r"\bdebug", r"\bfix\b", r"\bcorrig", r"\bimplement",
        r"\bfunção\b", r"\bfunction\b", r"\bdef\s+\w+\(", r"\bclass\s+\w+",
        r"```[a-z]*\n", r"\berror\b", r"\btraceback\b", r"\bbug\b",
    ]
    low = ["arquitetur", "design", "planej", "analis", "modelo de", "estrateg", "fluxo", "document"]
    q = query.lower()
    if any(re.search(p, q, re.I) for p in high):
        return True
    if any(kw in q for kw in low):
        return False
    return False


# ---------------------------------------------------------------- driver ---
async def run_council(query: str, top_n: int = 4, pool_path: str | None = None,
                      auto_persona: bool = True) -> CouncilResult:
    t0 = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    members, chairman = load_council_members(top_n, pool_path=pool_path)

    # Aplica persona automática: 1 membro vira "ponytail" se a tarefa é de código
    # (nunca chairman, nunca todos — diversidade é o objetivo)
    if auto_persona and _is_coding_task(query):
        ponytail_path = Path(__file__).parent / "personas" / "ponytail.md"
        for m in members[1:]:
            if m.persona is None and ponytail_path.exists():
                m.persona = "ponytail"
                break

    reserves = load_reserves({m.model for m in members}) if pool_path is None else []
    swaps: list[dict] = []
    s1 = await stage_one(query, members, reserves=reserves, swaps=swaps)
    anon, label_map = _anonymize(s1)
    s2, label_map = await stage_two(query, s1, members)

    # Chairman: o MAIN se respondeu no estágio 1; senão (ou se a síntese falhar)
    # o próximo membro que respondeu, em ordem de prioridade.
    answered = [m for m in members if any(s.member == m.name and not s.error for s in s1)]
    order = ([chairman] if chairman in members else []) + [m for m in answered if m is not chairman]
    if not answered:
        raise RuntimeError("Nenhum membro respondeu (nem os reservas).")
    final, chairman_used, last_err = "", "", None
    for i, cand in enumerate(order):
        try:
            final = await asyncio.wait_for(
                stage_three(query, s1, s2, label_map, cand), timeout=MEMBER_TIMEOUT_S * 2)
            chairman_used = cand.model if i == 0 and cand is chairman else \
                f"{cand.model} (chairman substituto)"
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if not final:
        raise RuntimeError(f"Síntese falhou em todos os candidatos a chairman ({last_err})")

    return CouncilResult(
        query=query, started_at=started,
        stage1=s1, stage2=s2,
        chairman_model=chairman_used,
        final=final,
        total_elapsed_s=round(time.time() - t0, 2),
        swaps=swaps,
    )


def to_json(r: CouncilResult) -> str:
    return json.dumps(asdict(r), ensure_ascii=False, indent=2)


def to_markdown(r: CouncilResult) -> str:
    lines = [
        f"# Council — {r.started_at}",
        f"**Query:** {r.query}",
        f"**Chairman:** `{r.chairman_model}`",
        f"**Total:** {r.total_elapsed_s}s",
        "", "## Final answer", "", (r.final or "_(sem resposta final — todos os membros/chaiman falharam)_"), "",
        "---", "", "## Stage 1 — first opinions",
    ]
    if r.swaps:
        lines[-1:-1] = ["## Trocas automáticas de membro", ""] + [
            f"- slot {w['slot']}: `{w['from']}` → `{w['to']}` ({w['reason']})" for w in r.swaps
        ] + [""]
    for s in r.stage1:
        if s.error:
            lines.append(f"- **{s.member}** (`{s.model}`) — ❌ {s.error}")
        else:
            lines.append(f"- **{s.member}** (`{s.model}`, {s.elapsed_s:.1f}s):\n\n```\n{(s.text or '')[:1500]}\n```\n")
    lines.append("## Stage 2 — peer rankings")
    for s in r.stage2:
        if s.error:
            lines.append(f"- **{s.reviewer}** — ❌ {s.error}")
        else:
            lines.append(f"- **{s.reviewer}**: " + " > ".join(s.ranking or []))
    return "\n".join(str(x) for x in lines)


async def _amain():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--members", type=int, default=4)
    ap.add_argument("--no-ponytail", action="store_true",
                    help="desativa persona automática ponytail em query de código")
    ap.add_argument("--out", default=str(STATE_PATH / "last.json"))
    ap.add_argument("--md", default=str(STATE_PATH / "last.md"))
    ap.add_argument("--json", action="store_true", help="print JSON to stdout")
    args = ap.parse_args()

    q = " ".join(args.query)
    r = await run_council(q, top_n=args.members, auto_persona=not args.no_ponytail)
    Path(args.out).write_text(to_json(r), encoding="utf-8")
    Path(args.md).write_text(to_markdown(r), encoding="utf-8")
    if args.json:
        print(to_json(r))
    else:
        print(to_markdown(r))


def main():
    for s in (sys.stdout, sys.stderr):  # Windows: console cp1252
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
