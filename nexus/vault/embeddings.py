"""Swappable embedding interface.

Semantic search is OPTIONAL and dormant by default: with no embedding key configured,
`embed()` returns None and the search engine falls back to BM25-only. Turn it on by
setting EMBEDDING_API_KEY. The framework cares about THIS interface, not the backend —
swap the provider call below without touching search.py.

Default backend: Voyage AI (Anthropic's recommended embeddings partner). To use a
different provider, replace the body of `_embed_remote`.
"""

from __future__ import annotations

import httpx

from nexus.config import settings

_MODEL = "voyage-3"
_ENDPOINT = "https://api.voyageai.com/v1/embeddings"


def embed(texts: list[str]) -> list[list[float]] | None:
    """Return one dense vector per input text, or None if semantic search is dormant.

    Returning None (not raising) is the contract that lets every caller degrade to
    BM25-only with zero branching elsewhere.
    """
    if not settings.semantic_enabled or not texts:
        return None
    try:
        return _embed_remote(texts)
    except httpx.HTTPError:
        return None  # provider down / rate-limited -> degrade to BM25-only, don't break search


def _embed_remote(texts: list[str]) -> list[list[float]]:
    resp = httpx.post(
        _ENDPOINT,
        headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
        json={"input": texts, "model": _MODEL},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return [row["embedding"] for row in data]
