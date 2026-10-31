"""Retrieval: BM25 coarse recall, TF-IDF cosine rerank, then signal fusion.

Two-stage design, as requested: BM25 casts a wide net cheaply, then cosine
similarity over the same sparse space reranks the candidates. A weighted fusion
step folds in tag overlap, recency and declared importance so that a
high-value old memory can still outrank a low-value fresh one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

import synonyms
import textproc
from store import Entry, Vault, HALFLIFE_BY_KIND

INDEX_VERSION = 1

# Ranking weights. Only signals that are actually active for a query get
# normalized into the final score, so a tag-free query is not penalized.
W_BM25 = 0.42
W_COSINE = 0.26
W_TAG = 0.12
W_RECENCY = 0.10
W_IMPORTANCE = 0.10

# BM25 parameters.
K1 = 1.5
B = 0.75

# Normalizing BM25 by the best candidate alone makes weak matches look strong when
# the candidate pool is small (a two-entry store always has a "top" hit). Dividing
# by at least this value keeps the signal honest: a raw score of ~0.8 reads as weak
# instead of being inflated to 1.0. Roughly the score of a solid two-term match.
BM25_SOFT_CEILING = 4.0

# Project-scoped memories get a mild boost when searching inside that project.
SCOPE_BOOST = {"project": 1.12, "global": 1.0}


# --- Index building --------------------------------------------------------


def build_index(vaults: list[tuple[str, Vault]]) -> dict[str, Any]:
    """Build a combined index over one or more vaults."""
    docs: list[dict[str, Any]] = []
    df: dict[str, int] = {}
    all_tags: set[str] = set()
    total_len = 0

    for label, vault in vaults:
        for entry in vault.all_entries():
            tokens = textproc.tokenize(entry.searchable_text)
            if not tokens:
                continue
            tf = textproc.term_freq(tokens)
            docs.append(
                {
                    "id": entry.id,
                    "vault": label,
                    "root": str(vault.root),
                    "title": entry.title,
                    "kind": entry.kind,
                    "scope": entry.scope,
                    "project": entry.project,
                    "tags": entry.tags,
                    "importance": entry.importance,
                    "confidence": entry.confidence,
                    "created": entry.created,
                    "updated": entry.updated,
                    "expires": entry.expires,
                    "len": len(tokens),
                    "tf": tf,
                }
            )
            total_len += len(tokens)
            for token in tf:
                df[token] = df.get(token, 0) + 1
            all_tags.update(entry.tags)

    n = len(docs)
    return {
        "version": INDEX_VERSION,
        "built_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        # Recorded so a cached index is never reused from a different working
        # context -- otherwise memories from one project leak into another.
        "vault_roots": [f"{label}:{vault.root}" for label, vault in vaults],
        "n": n,
        "avgdl": (total_len / n) if n else 0.0,
        "df": df,
        "docs": docs,
        "tags": sorted(all_tags),
    }


def idf(df: dict[str, int], n: int, token: str) -> float:
    d = df.get(token, 0)
    return math.log(1.0 + (n - d + 0.5) / (d + 0.5))


# --- Scoring ---------------------------------------------------------------


def bm25_score(
    core_tokens: list[str],
    extra_tokens: list[str],
    doc: dict[str, Any],
    index: dict[str, Any],
    extra_weight: float = 0.45,
) -> float:
    """BM25 with a discounted contribution from concept-expansion tokens.

    Expansion tokens widen recall but must not dominate: a document matching only
    the expansion terms is weaker evidence than one matching the user's actual
    words.
    """
    tf = doc["tf"]
    dl = doc["len"] or 1
    avgdl = index["avgdl"] or 1.0
    n = index["n"]
    df = index["df"]
    core = set(core_tokens)
    score = 0.0

    for token in core:
        freq = tf.get(token)
        if not freq:
            continue
        denom = freq + K1 * (1 - B + B * dl / avgdl)
        score += idf(df, n, token) * (freq * (K1 + 1)) / denom

    for token in set(extra_tokens) - core:
        freq = tf.get(token)
        if not freq:
            continue
        denom = freq + K1 * (1 - B + B * dl / avgdl)
        score += extra_weight * idf(df, n, token) * (freq * (K1 + 1)) / denom

    return score


def doc_vector(doc: dict[str, Any], index: dict[str, Any]) -> dict[str, float]:
    """L2-normalized TF-IDF sparse vector."""
    n = index["n"]
    df = index["df"]
    vec: dict[str, float] = {}
    for token, freq in doc["tf"].items():
        weight = (1.0 + math.log(freq)) * idf(df, n, token)
        if weight > 0:
            vec[token] = weight
    norm = math.sqrt(sum(w * w for w in vec.values()))
    if norm <= 0:
        return {}
    return {t: w / norm for t, w in vec.items()}


def query_vector(query_tokens: list[str], index: dict[str, Any]) -> dict[str, float]:
    n = index["n"]
    df = index["df"]
    counts = textproc.term_freq(query_tokens)
    vec: dict[str, float] = {}
    for token, freq in counts.items():
        weight = (1.0 + math.log(freq)) * idf(df, n, token)
        if weight > 0:
            vec[token] = weight
    norm = math.sqrt(sum(w * w for w in vec.values()))
    if norm <= 0:
        return {}
    return {t: w / norm for t, w in vec.items()}


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(t, 0.0) for t, w in a.items())


def recency_signal(doc: dict[str, Any], now: datetime) -> float:
    """Exponential decay; slow-decaying kinds keep their value longer."""
    half_life = HALFLIFE_BY_KIND.get(doc.get("kind", "fact"), 270.0)
    try:
        raw = str(doc.get("updated") or doc.get("created") or "").replace("Z", "+00:00")
        ts = datetime.fromisoformat(raw)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=now.tzinfo)
        age = max(0.0, (now - ts).total_seconds() / 86400.0)
    except (ValueError, TypeError):
        return 0.5
    return math.exp(-math.log(2) * age / half_life)


def tag_signal(query_tags: list[str], doc_tags: list[str]) -> float | None:
    if not query_tags:
        return None
    doc_set = {t.lower() for t in doc_tags}
    hits = sum(1 for t in query_tags if t.lower() in doc_set)
    return hits / len(query_tags)


# --- Search ----------------------------------------------------------------


@dataclass
class Hit:
    doc: dict[str, Any]
    score: float
    bm25: float
    cosine: float
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.doc["id"]


def is_expired(doc: dict[str, Any], now: datetime) -> bool:
    raw = doc.get("expires")
    if not raw:
        return False
    try:
        text = str(raw).replace("Z", "+00:00")
        if len(text) == 10:
            exp = datetime.fromisoformat(text).replace(tzinfo=now.tzinfo)
        else:
            exp = datetime.fromisoformat(text)
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=now.tzinfo)
        return exp < now
    except ValueError:
        return False


def search(
    query: str,
    index: dict[str, Any],
    limit: int = 8,
    kinds: Iterable[str] | None = None,
    scopes: Iterable[str] | None = None,
    tags: Iterable[str] | None = None,
    project: str | None = None,
    include_expired: bool = False,
    min_score: float = 0.0,
    now: datetime | None = None,
) -> list[Hit]:
    now = now or datetime.now().astimezone()
    core_tokens = textproc.tokenize(query)
    if not core_tokens:
        return []
    # Concept expansion widens the coarse stage only; reranking stays anchored to
    # what the user actually typed.
    extra_tokens = synonyms.expand_tokens(core_tokens)

    kind_set = {k.lower() for k in kinds} if kinds else None
    scope_set = {s.lower() for s in scopes} if scopes else None
    tag_set = {t.lower() for t in tags} if tags else None
    query_tags = textproc.extract_tags_from_query(query, set(index.get("tags") or []))

    # Stage 0: hard filters.
    pool = []
    for doc in index["docs"]:
        if kind_set and doc["kind"].lower() not in kind_set:
            continue
        if scope_set and doc["scope"].lower() not in scope_set:
            continue
        if tag_set and not (tag_set & {t.lower() for t in doc["tags"]}):
            continue
        if project and doc["scope"] == "project" and doc["project"] != project:
            continue
        if not include_expired and is_expired(doc, now):
            continue
        pool.append(doc)

    if not pool:
        return []

    # Stage 1: BM25 coarse recall, widened to catch semantically close items.
    coarse_n = max(limit * 5, 40)
    scored = [(bm25_score(core_tokens, extra_tokens, doc, index), doc) for doc in pool]
    scored = [pair for pair in scored if pair[0] > 0]
    scored.sort(key=lambda p: p[0], reverse=True)
    candidates = scored[:coarse_n]
    if not candidates:
        return []

    # Stage 2: rerank with cosine + auxiliary signals.
    qvec = query_vector(core_tokens, index)
    max_bm25 = max(s for s, _ in candidates) or 1.0
    hits: list[Hit] = []

    for raw_bm25, doc in candidates:
        dvec = doc_vector(doc, index)
        cos = cosine(qvec, dvec)
        signals: dict[str, float] = {
            "bm25": min(1.0, raw_bm25 / max(max_bm25, BM25_SOFT_CEILING)),
            "cosine": max(0.0, min(1.0, cos)),
            "recency": recency_signal(doc, now),
            "importance": (doc["importance"] - 1) / 4.0,
        }
        # If the user's actual words never appear in the entry, cosine is 0 by
        # construction and carries no information -- the match came entirely from
        # concept expansion. Drop the signal rather than let it drag the score down.
        if cos <= 0.0 and not any(t in doc["tf"] for t in set(core_tokens)):
            signals.pop("cosine")

        t_sig = tag_signal(query_tags, doc["tags"])
        if t_sig is not None:
            signals["tag"] = t_sig

        weights = {
            "bm25": W_BM25,
            "cosine": W_COSINE,
            "tag": W_TAG,
            "recency": W_RECENCY,
            "importance": W_IMPORTANCE,
        }
        active = [(weights[k], v) for k, v in signals.items() if k in weights]
        total_w = sum(w for w, _ in active) or 1.0
        fused = sum(w * v for w, v in active) / total_w
        fused *= SCOPE_BOOST.get(doc["scope"], 1.0)

        if fused >= min_score:
            hits.append(Hit(doc=doc, score=fused, bm25=raw_bm25, cosine=cos, signals=signals))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def digest(index: dict[str, Any], limit: int = 12, now: datetime | None = None) -> list[Hit]:
    """Highest-value memories without a query -- for session warm-up."""
    now = now or datetime.now().astimezone()
    hits = []
    for doc in index["docs"]:
        if is_expired(doc, now):
            continue
        value = (
            0.55 * ((doc["importance"] - 1) / 4.0)
            + 0.30 * recency_signal(doc, now)
            + 0.15 * SCOPE_BOOST.get(doc["scope"], 1.0)
        )
        hits.append(
            Hit(
                doc=doc,
                score=value,
                bm25=0.0,
                cosine=0.0,
                signals={"importance": (doc["importance"] - 1) / 4.0,
                         "recency": recency_signal(doc, now)},
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]
