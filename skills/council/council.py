# Hermes Council — Mixture-of-Agents dinâmico sobre um perfil Hermes
# Lê $HERMES_HOME/config.yaml a cada execução (HERMES_HOME = diretório do
# perfil, ex.: ~/.hermes/profiles/<perfil>; padrão ~/.hermes). O seletor
# free-LLM v4 atualiza model.default, moa.* e fallback_providers.
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

HERMES_HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()
CONFIG_PATH = HERMES_HOME / "config.yaml"
ENV_PATH = HERMES_HOME / ".env"
STATE_PATH = Path(os.environ.get("COUNCIL_STATE_DIR") or Path(__file__).resolve().parent / "state")
STATE_PATH.mkdir(parents=True, exist_ok=True)


def _load_profile_env():
    """O perfil guarda chaves em $HERMES_HOME/.env.
    Carrega em os.environ sem sobrescrever valores já definidos."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
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
    text = p.read_text()
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
    }.get(provider, [])
    for name in candidates:
        if os.environ.get(name):
            return os.environ[name]
    return None


def load_council_members(top_n: int = 4, pool_path: str | None = None) -> tuple[list[Member], Member]:
    """Lê config.yaml e devolve (members, chairman).
    Chairman = model.default (o mais bem ranqueado pelo seletor v4).
    Members  = chairman + moa.aggregator + moa.reference_models, completando
               com fallback_providers; dedupe por modelo; só slots com chave.

    Se pool_path for dado (JSON gerado por pool.py), usa essa pool verificada
    em vez do config — útil quando slots do config estão em rate limit.
    """
    if pool_path and Path(pool_path).exists():
        pool = json.loads(Path(pool_path).read_text())
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

    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    model = cfg["model"]
    chairman_entry = {
        "provider": model["provider"],
        "model": model["default"],
        "base_url": model.get("base_url", ""),
        "api_mode": model.get("api_mode", "chat_completions"),
    }
    # Ordem de prioridade: MAIN (chairman) → aggregator MoA → reference_models MoA
    # → fallback_providers (reserva). O seletor v4 escolhe os slots MoA por
    # qualidade (papel moa) e fabricantes distintos — melhor para opinião cruzada
    # que os fallbacks (escolhidos por cobertura de falha).
    moa = cfg.get("moa") or {}
    preset = ((moa.get("presets") or {}).get(moa.get("default_preset") or "default") or {})
    aggregator = preset.get("aggregator") or moa.get("aggregator")
    refs = preset.get("reference_models") or moa.get("reference_models") or []
    refs = [r for r in refs if r.get("enabled", True)]
    fallbacks = cfg.get("fallback_providers", [])

    candidates = [chairman_entry]
    if aggregator:
        candidates.append(aggregator)
    candidates += refs + fallbacks

    members: list[Member] = []
    seen: set[str] = set()
    for entry in candidates:
        if len(members) >= top_n:
            break
        mid = str(entry.get("model") or "")
        # dedupe por modelo (ignora :free) — o mesmo modelo em 2 slots não soma opinião
        key = mid.lower().removesuffix(":free")
        if not mid or key in seen or entry.get("provider") == "moa":
            continue
        api_key = _resolve_api_key(entry)
        base_url = entry.get("base_url") or ""
        if not api_key or not base_url:
            continue
        seen.add(key)
        alias = f"m{len(members)+1}-{entry['model'].split('/')[-1][:20]}"
        members.append(Member(
            name=alias,
            provider=entry["provider"],
            model=entry["model"],
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            api_mode=entry.get("api_mode", "chat_completions"),
        ))

    if len(members) < 2:
        raise RuntimeError(f"Council precisa de >=2 membros com chave; achei {len(members)}")
    chairman = members[0]  # model.default
    return members, chairman


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
async def stage_one(query: str, members: list[Member]) -> list[StageOneResult]:
    """Cada membro responde a query independentemente, em paralelo."""
    async with httpx.AsyncClient() as client:
        async def one(m: Member) -> StageOneResult:
            t0 = time.time()
            try:
                text = await _chat(client, m, [{"role": "user", "content": query}])
                return StageOneResult(m.name, m.model, text, time.time() - t0)
            except Exception as e:
                return StageOneResult(m.name, m.model, "", time.time() - t0, error=str(e))
        return await asyncio.gather(*[one(m) for m in members])


def _anonymize(stage1: list[StageOneResult]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Devolve [(label, text)] e map label->member_name."""
    valid = [s for s in stage1 if not s.error and s.text]
    labels = [chr(ord("A") + i) for i in range(len(valid))]
    anon = [(f"Response {lab}", s.text) for lab, s in zip(labels, valid)]
    label_to_member = {f"Response {lab}": s.member for lab, s in zip(labels, valid)}
    return anon, label_to_member


async def stage_two(query: str, stage1: list[StageOneResult], members: list[Member]) -> tuple[list[StageTwoReview], dict[str, str]]:
    anon, label_to_member = _anonymize(stage1)
    if len(anon) < 2:
        return [], label_to_member

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
                raw = await _chat(client, m, [{"role": "user", "content": prompt}],
                                  temperature=0.0, max_tokens=512)
                # extração tolerante a markdown
                s = raw.strip()
                if s.startswith("```"):
                    s = s.split("```")[1].lstrip("json").strip()
                start = s.find("["); end = s.rfind("]")
                ranking = json.loads(s[start:end+1]) if start >= 0 else []
                return StageTwoReview(m.name, m.model, ranking, raw)
            except Exception as e:
                return StageTwoReview(m.name, m.model, [], "", error=str(e))
        reviews = await asyncio.gather(*[one(m) for m in members])
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

    s1 = await stage_one(query, members)
    anon, label_map = _anonymize(s1)
    s2, label_map = await stage_two(query, s1, members)
    try:
        final = await stage_three(query, s1, s2, label_map, chairman)
        chairman_used = chairman.model
    except Exception as e:
        # chairman indisponível → primeiro membro que respondeu assume
        fallback = next((m for m in members if any(s.member == m.name and not s.error for s in s1)), None)
        if fallback is None:
            raise RuntimeError(f"Chairman falhou ({e}) e nenhum membro respondeu")
        final = await stage_three(query, s1, s2, label_map, fallback)
        chairman_used = f"{fallback.model} (fallback: chairman falhou)"

    return CouncilResult(
        query=query, started_at=started,
        stage1=s1, stage2=s2,
        chairman_model=chairman_used,
        final=final,
        total_elapsed_s=round(time.time() - t0, 2),
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
    Path(args.out).write_text(to_json(r))
    Path(args.md).write_text(to_markdown(r))
    if args.json:
        print(to_json(r))
    else:
        print(to_markdown(r))


def main():
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
