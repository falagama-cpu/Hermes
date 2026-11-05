#!/usr/bin/env python3
"""C3.9 — Classificador de gratuidade NVIDIA NIM baseado no catálogo OFICIAL.

Fonte primária: https://build.nvidia.com/models (badge "Free Endpoint").
Fonte secundária (identificador técnico): GET https://integrate.api.nvidia.com/v1/models

PRINCÍPIOS (C3.9):
  1. A gratuidade vem da INDICAÇÃO OFICIAL "Free Endpoint" — NUNCA de HTTP 200.
  2. HTTP 200/429 é sinal de DISPONIBILIDADE, separado do preço.
  3. Falha de probe NÃO classifica como pago.
  4. O identificador técnico precisa ser CONFIRMADO no /models da API.
  5. Modelos sem ID confirmável ficam PENDENTES (não entram no pool).
  6. Não-conversacionais (embedding/rerank/tradução/TTS/visão/etc.) ficam de fora
     do pool conversacional do Hermes.
  7. Restrições de uso (Trial ToS) são registradas separadamente do preço.

Módulo standalone e importável: sem rede no import, sem side-effects.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------
NVIDIA_CATALOG_URL = "https://build.nvidia.com/models"
NVIDIA_CATALOG_PREVIEW_FILTER = "nimType%3Anim_type_preview"
NVIDIA_API_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_CHAT_ENDPOINT = "chat/completions"

CATALOG_TTL_S = 24 * 3600          # catálogo oficial revalidado 1x/dia
API_MODELS_TTL_S = 24 * 3600       # /models da API revalidado 1x/dia

# Termos que provam que o modelo NÃO é um LLM conversacional.
# Avaliados contra slug + display + descrição.
NON_CHAT_MODALITIES: Dict[str, Tuple[str, ...]] = {
    "embeddings": (
        "embedding", "embed-", "-embed", "embedqa", "nemoretriever",
        "retriever", "text-to-embedding",
    ),
    "reranking": ("rerank",),
    "safety": (
        "guard", "content-safety", "nemoguard", "safety-guard",
        "safety_classifier", "llama-guard",
    ),
    "tts": (
        "tts", "text-to-speech", "studiovoice", "magpie", "voicechat",
        "voice-clone", "speech-generation",
    ),
    "speech": (
        "asr", "whisper", "speech-to-text", "active-speaker", "bnr",
        "background-noise", "synthetic-video-detector",
    ),
    "translation": ("translate", "translation", "riva-translate"),
    "vision_estimation": (
        "body-pose", "bevformer", "streampetr", "cosmos", "sparsedrive",
        "driveby", "occupancy", "detection",
    ),
    "physics": ("ising-calibration",),
    "tabular": ("kumo-tabular", "kumo-relational"),
    "document_parsing": ("nemotron-parse", "nemotron-3.5-parse", "document-parsing"),
    "science": ("proteina", "rfd4", "alphafold", "boltz"),
    "image_generation": ("diffusiongemma", "cosmos-transfer", "image-generation"),
    "tabular_ml": ("deplot",),
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
@dataclass
class NvFreeEntry:
    """Uma entrada do catálogo oficial NVIDIA NIM."""

    display_name: str                       # nome exibido no catálogo
    technical_id: Optional[str]            # ID exato da API (publisher/model)
    publisher: str
    slug: str                              # slug da página no build.nvidia.com
    modality: str                          # text_chat | embeddings | ...
    endpoint: Optional[str]                # chat/completions | embeddings | ...
    is_preview: bool
    is_free_endpoint: bool                 # badge oficial
    is_downloadable: bool                  # "Downloadable Free Endpoint"
    last_verified: str                     # ISO timestamp da última checagem
    last_updated_official: Optional[str] = None   # "Last updated on ..." do catálogo
    usage_restrictions: List[str] = field(default_factory=list)
    license: Optional[str] = None
    availability: str = "unknown"          # available | quota_exhausted | unavailable | unknown
    http_status: Optional[int] = None      # status do probe (NÃO prova gratuidade)
    hermes_class: str = "pendente_validacao"
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Parse do catálogo oficial
# ---------------------------------------------------------------------------
_BADGE_RE = re.compile(r'data-testid="nv-badge">(.*?)</span>', re.S)
_CARD_RE = re.compile(r'data-testid="nv-card-root"')
_HREF_RE = re.compile(r'href="(/[a-z0-9_\-]+/[a-z0-9_\-\.]+)"')
_TITLE_RE = re.compile(r"<h3><a[^>]*>([^<]{1,120})</a>")
_UPDATED_RE = re.compile(r"Last updated on ([A-Za-z]+ \d{1,2}, \d{4})")
_FM_UPDATED_RE = re.compile(r'^updated:\s*"?([^"\n]+)"?\s*$', re.M)
_FM_TITLE_RE = re.compile(r'^title:\s*"?([^"\n]+)"?\s*$', re.M)
_FM_DESC_RE = re.compile(r'^description:\s*"?([^"\n]+)"?\s*$', re.M)
_FM_PUB_RE = re.compile(r'^publisher:\s*"?([^"\n]+)"?\s*$', re.M)
_TRIAL_TOS_RE = re.compile(r"API Trial Terms of Service", re.I)
_LICENSE_RE = re.compile(r"Use of the model is governed by the \[([^\]]+)\]")


def parse_official_catalog(html: str) -> List[Dict[str, Any]]:
    """Extrai os cards do catálogo oficial, marcando os que têm badge Free Endpoint.

    O catálogo usa um card por modelo (`data-testid="nv-card-root"`) com UM OU
    MAIS badges. "Downloadable" (artefato baixável) e "Free Endpoint"
    (endpoint hospedado gratuito) são badges DISTINTOS e podem coexistir no
    mesmo card — é obrigatório checar a presença de "Free Endpoint", nunca
    inferir gratuidade a partir de "Downloadable" nem de HTTP 200.

    O texto do título vem de <span data-testid="nv-text"> dentro do <h3>.
    """
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for chunk in html.split('data-testid="nv-card-root"')[1:]:
        seg = chunk[:9000]
        badges = [b.strip() for b in _BADGE_RE.findall(seg[:4000], re.S)]
        if "Free Endpoint" not in badges:
            continue
        m = _HREF_RE.search(seg)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        mt = re.search(
            r'href="' + re.escape(slug) + r'"[^>]*>\s*<span[^>]*data-testid="nv-text"[^>]*>([^<]{1,120})</span>',
            seg,
        )
        if not mt:
            mt = _TITLE_RE.search(seg)
        mu = _UPDATED_RE.search(seg)
        out.append({
            "slug": slug,
            "publisher": slug.split("/")[1],
            "display_name": mt.group(1).strip() if mt else slug.split("/")[2],
            "is_free_endpoint": True,
            "is_downloadable": "Downloadable" in badges,
            "is_deprecation": any("Deprecation" in b for b in badges),
            "badges": badges,
            "last_updated_official": mu.group(1) if mu else None,
        })
    return out


def classify_modality(slug: str, display: str, description: str) -> str:
    """Classifica a modalidade a partir do texto. 'text_chat' = conversacional.

    Regra 3 do C3.9: embeddings, reranking, transcrição, tradução e visão
    NÃO entram automaticamente na lista de modelos conversacionais.
    """
    hay = f"{slug} {display} {description}".lower()
    for modality, terms in NON_CHAT_MODALITIES.items():
        for term in terms:
            if term in hay:
                return modality
    return "text_chat"


def endpoint_for_modality(modality: str) -> Optional[str]:
    if modality == "text_chat":
        return NVIDIA_CHAT_ENDPOINT
    if modality == "embeddings":
        return "embeddings"
    return None


# ---------------------------------------------------------------------------
# Resolução do identificador técnico
# ---------------------------------------------------------------------------
def _norm(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.lower())


def resolve_technical_id(
    slug: str,
    api_ids: Iterable[str],
    md_text: str = "",
    html: str = "",
) -> Tuple[Optional[str], str]:
    """Resolve o ID técnico exato da API para um slug do catálogo.

    Retorna (technical_id|None, source). Fontes em ordem de confiança:
      md_exact          — code sample do .md oficial
      api_exact         — slug + publisher casa 1:1 com /models
      api_variant       — variantes de separador (- vs _ vs .)
      api_normalized    — normalização de separadores
      api_suffix        — sufixo único no /models
      unresolved        — NENHUM identificador (=> pendente, nunca inventar)
    """
    api_ids = list(api_ids)
    api_set = set(api_ids)
    publisher = slug.split("/")[1]
    tail = slug.split("/")[2]

    cands: Set[str] = set()
    for pat in (
        r'model:\s*"([^"]+)"',
        r'model=\\?"([^"\\]+)\\?"',
        r'"model":\s*"([^"]+)"',
        r'model:\\?"([^"\\]+)\\"',
    ):
        for hit in re.findall(pat, md_text or ""):
            if "/" in hit and "nvidia.com" not in hit and not hit.startswith(("http", "local/")):
                cands.add(hit)
    exact = sorted(c for c in cands if c in api_set)
    if exact:
        return exact[0], "md_exact"

    variants = [
        f"{publisher}/{tail}",
        f"{publisher}/{tail.replace('_', '-')}",
        f"{publisher}/{tail.replace('_', '.')}",
        f"{publisher}/{tail.replace('-', '.')}",
        f"{publisher}/{tail.replace('-', '_')}",
    ]
    for v in variants:
        if v in api_set:
            return v, "api_exact"

    norm_map = {_norm(i): i for i in api_ids}
    for v in variants:
        if _norm(v) in norm_map:
            return norm_map[_norm(v)], "api_normalized"

    suffix = _norm(tail)
    hits = [i for i in api_ids if _norm(i.split("/")[-1]) == suffix]
    if len(hits) == 1:
        return hits[0], "api_suffix"

    return None, "unresolved"


# ---------------------------------------------------------------------------
# Availability (separada de preço)
# ---------------------------------------------------------------------------
PERMANENT_HTTP = {400, 401, 402, 403, 404, 410, 422}


def classify_availability(
    http_status: Optional[int],
    official_free: bool,
    quota_exhausted: bool = False,
) -> str:
    """Traduz o resultado do probe em estado de DISPONIBILIDADE.

    Nunca devolve 'pago'. A gratuidade vem de `official_free`; o probe só
    informa se o endpoint está no ar, com cota, ou fora do ar.
    """
    if quota_exhausted:
        return "quota_exhausted"
    if http_status is None:
        return "unknown"
    if http_status == 200:
        return "available"
    if http_status == 429:
        return "rate_limited"
    if http_status in PERMANENT_HTTP:
        return "unavailable"
    if http_status == 0:
        return "network_error"
    if 500 <= http_status < 600:
        return "upstream_error"
    return "unknown"


def is_free_endpoint(entry: NvFreeEntry) -> bool:
    """Única fonte de verdade de gratuidade: o badge oficial do catálogo."""
    return bool(entry.is_free_endpoint)


# ---------------------------------------------------------------------------
# Classificação final
# ---------------------------------------------------------------------------
def classify_for_hermes(entry: NvFreeEntry) -> str:
    """Classifica a entrada para o pool FREE conversacional do Hermes.

    - 'elegivel'                 -> Free Endpoint oficial + ID confirmado + chat
    - 'nao_conversacional'       -> modalidade sem chat (embedding/rerank/...)
    - 'pendente_validacao'       -> sem ID técnico confirmável
    - 'nao_confirmado_free'      -> sem badge oficial de Free Endpoint

    Precedência: excluir por modalidade ANTES de exigir o ID técnico.
    Um embedding/rerank/parse nunca deve entrar no pool conversacional,
    mesmo que o identificador exato não seja resolvido — excluir é seguro,
    incluir exige prova. O estado 'pendente_validacao' fica reservado para
    casos em que a modalidade É conversacional mas o ID não pôde ser
    confirmado (regra 10: nunca inventar o identificador).
    """
    if not entry.is_free_endpoint:
        return "nao_confirmado_free"
    if entry.modality != "text_chat" or entry.endpoint != NVIDIA_CHAT_ENDPOINT:
        return "nao_conversacional"
    if not entry.technical_id:
        return "pendente_validacao"
    return "elegivel"


def build_entries(
    catalog_cards: List[Dict[str, Any]],
    api_ids: Iterable[str],
    md_pages: Optional[Dict[str, str]] = None,
    probe_status: Optional[Dict[str, int]] = None,
    now: Optional[float] = None,
) -> List[NvFreeEntry]:
    """Monta as entradas finais cruzando catálogo oficial + /models + probe."""
    md_pages = md_pages or {}
    probe_status = probe_status or {}
    api_ids = list(api_ids)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time()))

    entries: List[NvFreeEntry] = []
    for card in catalog_cards:
        slug = card["slug"]
        md = md_pages.get(slug, "")
        fm_title = _FM_TITLE_RE.search(md)
        fm_desc = _FM_DESC_RE.search(md)
        fm_pub = _FM_PUB_RE.search(md)
        fm_upd = _FM_UPDATED_RE.search(md)
        display = fm_title.group(1).strip() if fm_title else card.get("display_name") or slug.split("/")[2]
        desc = fm_desc.group(1).strip() if fm_desc else ""
        publisher = fm_pub.group(1).strip() if fm_pub else card.get("publisher") or slug.split("/")[1]

        tid, tid_src = resolve_technical_id(slug, api_ids, md_text=md)
        modality = classify_modality(slug, display, desc)
        is_preview = bool(re.search(r"\bpreview\b|early[- ]access", md, re.I)) or "preview" in slug.lower()

        restrictions: List[str] = []
        if _TRIAL_TOS_RE.search(md):
            restrictions.append(
                "NVIDIA API Trial Terms of Service — acesso gratuito para "
                "desenvolvimento/testes; não é autorização irrestrita de produção"
            )
        if card.get("is_downloadable"):
            restrictions.append("Downloadable Free Endpoint — artefato baixável, não endpoint hospedado")

        entry = NvFreeEntry(
            display_name=display,
            technical_id=tid,
            publisher=publisher,
            slug=slug,
            modality=modality,
            endpoint=endpoint_for_modality(modality),
            is_preview=is_preview,
            is_free_endpoint=bool(card.get("is_free_endpoint")),
            is_downloadable=bool(card.get("is_downloadable")),
            last_verified=stamp,
            last_updated_official=(fm_upd.group(1).strip() if fm_upd
                                   else card.get("last_updated_official")),
            usage_restrictions=restrictions,
            license=(_LICENSE_RE.search(md).group(1) if _LICENSE_RE.search(md) else None),
            availability="unknown",
            http_status=probe_status.get(tid) if tid else None,
            notes=tid_src,
        )
        entry.availability = classify_availability(entry.http_status, entry.is_free_endpoint)
        entry.hermes_class = classify_for_hermes(entry)
        entries.append(entry)
    return entries


def eligible_ids(entries: Iterable[NvFreeEntry]) -> List[str]:
    """IDs técnicos dos modelos elegíveis ao pool FREE conversacional."""
    return sorted(e.technical_id for e in entries
                  if e.hermes_class == "elegivel" and e.technical_id)


# ---------------------------------------------------------------------------
# Cache em disco
# ---------------------------------------------------------------------------
def load_cache(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data


def is_cache_fresh(cache: Optional[Dict[str, Any]], now: Optional[float] = None) -> bool:
    if not cache:
        return False
    ts = float(cache.get("fetched_at") or 0)
    age = (now if now is not None else time.time()) - ts
    return age < CATALOG_TTL_S


def save_cache(path: Path, payload: Dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)
