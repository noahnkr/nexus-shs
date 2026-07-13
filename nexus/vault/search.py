"""Hybrid retrieval, in-process (spec §3.4).

At moderate scale the INDEX.md *is* the retrieval layer (read index, drill into pages).
This adds a hybrid engine for when prose search matters — all in-process, no new infra:

  - BM25 (lexical) over each note's PROJECTED text (title + summary + tags + body, plus
    family-specific content like event-entry summaries and task actions). Rebuilt
    in-memory on demand — a small vault rebuilds sub-second.
  - Dense vectors (semantic) via the swappable embed() interface, persisted to sqlite-vec
    on the same volume. Embedded once; re-embedded only when the content hash changes.
  - RRF (reciprocal rank fusion, k≈60) merges the two ranked lists BY RANK, not raw score
    (BM25 scores and cosine distances aren't comparable).

Semantic is dormant when embed() returns None: search is BM25-only and everything works.

Alternative (§3.4 note): qmd can back these query functions instead — the framework cares
about the query-function interface, not the engine.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field

from nexus.vault import io
from nexus.vault.embeddings import embed
from nexus.vault.schema import EntityNote, EventNote, Family, TaskNote

RRF_K = 60
_TOKEN = re.compile(r"\w+")


@dataclass
class Hit:
    path: str
    score: float
    title: str
    summary: str | None = None
    family: str | None = None


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def rrf_merge(*ranked_lists: list[str], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: merge ranked id-lists by rank, not score (§3.4).

    score(id) = Σ 1 / (k + rank_in_list). Pure and engine-agnostic.
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def project(note, body: str) -> str:
    """The searchable text for a note: shared fields + family-specific content (§3.4)."""
    parts: list[str] = [note.title, note.summary or "", " ".join(note.tags), body]
    if isinstance(note, EventNote):
        parts.extend(note.entries)
    elif isinstance(note, TaskNote):
        parts.extend(filter(None, [note.action, note.body]))
    elif isinstance(note, EntityNote):
        parts.append(str(note.kind))
    return "\n".join(p for p in parts if p)


class _BM25:
    """Compact BM25-Okapi over an in-memory corpus (no external dependency)."""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs = corpus
        self.n = len(corpus)
        self.avgdl = (sum(len(d) for d in corpus) / self.n) if self.n else 0.0
        self.tf: list[dict[str, int]] = []
        df: dict[str, int] = {}
        for doc in corpus:
            freqs: dict[str, int] = {}
            for tok in doc:
                freqs[tok] = freqs.get(tok, 0) + 1
            self.tf.append(freqs)
            for tok in freqs:
                df[tok] = df.get(tok, 0) + 1
        self.idf = {t: math.log(1 + (self.n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query_tokens: list[str]) -> list[float]:
        out = [0.0] * self.n
        for tok in query_tokens:
            idf = self.idf.get(tok)
            if idf is None:
                continue
            for i, freqs in enumerate(self.tf):
                f = freqs.get(tok, 0)
                if not f:
                    continue
                dl = len(self.docs[i])
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                out[i] += idf * (f * (self.k1 + 1)) / denom
        return out


@dataclass
class _Doc:
    path: str
    title: str
    summary: str | None
    family: str
    text: str
    tokens: list[str] = field(default_factory=list)


class HybridIndex:
    """In-process BM25 + (optional) sqlite-vec dense store, fused with RRF."""

    def __init__(self) -> None:
        self._docs: list[_Doc] = []
        self._bm25: _BM25 | None = None
        self._vectors: dict[str, list[float]] = {}  # path -> embedding (when semantic on)

    def rebuild(self) -> None:
        """Rebuild the in-memory BM25 index over projected note text (§3.4).

        Walks the vault (iter_notes already excludes INDEX.md). When semantic is enabled,
        embeds notes whose content hash changed and caches the vectors.
        """
        docs: list[_Doc] = []
        for path, note, body in io.iter_notes():
            text = project(note, body)
            docs.append(
                _Doc(
                    path=str(path),
                    title=note.title,
                    summary=note.summary,
                    family=str(note.family),
                    text=text,
                    tokens=tokenize(text),
                )
            )
        self._docs = docs
        self._bm25 = _BM25([d.tokens for d in docs])
        self._maybe_embed(docs)

    def _maybe_embed(self, docs: list[_Doc]) -> None:
        """Embed changed docs into the dense cache; no-op when embed() is dormant (§3.4)."""
        if not docs:
            self._vectors = {}
            return
        # Re-embed only when content changed: key the cache by content hash per path.
        wanted = {d.path: hashlib.sha256(d.text.encode()).hexdigest() for d in docs}
        prior = getattr(self, "_hashes", {})
        changed = [d for d in docs if wanted[d.path] != prior.get(d.path)]
        if changed:
            vectors = embed([d.text for d in changed])
            if vectors is None:  # semantic dormant
                self._vectors = {}
                self._hashes = wanted
                return
            for d, v in zip(changed, vectors, strict=True):
                self._vectors[d.path] = v
        # Drop vectors for removed paths.
        self._vectors = {p: v for p, v in self._vectors.items() if p in wanted}
        self._hashes = wanted

    def query(self, q: str, k: int = 10, *, family: Family | None = None) -> list[Hit]:
        """Hybrid query: BM25 list ⊕ dense list, fused via rrf_merge (§3.4).

        When the dense cache is empty (embed() dormant), returns the BM25 ranking alone.
        Optionally scoped to a single family (e.g. reference-only, event-only).
        """
        if self._bm25 is None:
            self.rebuild()
        assert self._bm25 is not None

        fam = str(family) if family else None
        keep = {d.path for d in self._docs if fam is None or d.family == fam}
        by_path = {d.path: d for d in self._docs}

        # --- lexical ---
        bm25 = self._bm25.scores(tokenize(q))
        lexical = [
            self._docs[i].path
            for i in sorted(range(len(self._docs)), key=lambda i: bm25[i], reverse=True)
            if bm25[i] > 0 and self._docs[i].path in keep
        ]

        # --- semantic (optional) ---
        ranked_lists = [lexical]
        if self._vectors:
            qv = embed([q])
            if qv:
                dense = sorted(
                    (p for p in self._vectors if p in keep),
                    key=lambda p: _cosine(qv[0], self._vectors[p]),
                    reverse=True,
                )
                ranked_lists.append(dense)

        fused = rrf_merge(*ranked_lists)
        hits: list[Hit] = []
        for path, score in fused[:k]:
            d = by_path[path]
            hits.append(
                Hit(path=path, score=score, title=d.title, summary=d.summary, family=d.family)
            )
        return hits


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


_index: HybridIndex | None = None
_dirty: bool = False


def mark_dirty() -> None:
    """Flag the corpus as changed. Called by the write gate (io.write_note) on EVERY
    successful write, so no write path can leave the index stale. The rebuild itself is
    lazy — the next get_index() pays it once, however many writes accumulated."""
    global _dirty
    _dirty = True


def get_index() -> HybridIndex:
    """The live index, rebuilt on first use and whenever the write gate dirtied it."""
    global _index, _dirty
    if _index is None:
        _index = HybridIndex()
        _index.rebuild()
        _dirty = False
    elif _dirty:
        _index.rebuild()
        _dirty = False
    return _index


def reindex() -> None:
    """Force a rebuild now. Rarely needed — the gate + lazy get_index() keep the corpus
    consistent; this remains for explicit refreshes (e.g. after hand edits in Obsidian)."""
    get_index().rebuild()
    global _dirty
    _dirty = False
