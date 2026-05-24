from __future__ import annotations

import logging
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import litellm
import numpy as np

from src.models import Item

log = logging.getLogger(__name__)

# Hard cap for batch embedding calls. Gemini rejects batches >100;
# other providers allow more but 100 is a safe universal limit.
_EMBED_BATCH_LIMIT = 100

_TRACKING_PARAMS_EXACT = {"ref", "fbclid", "gclid", "mc_cid", "mc_eid", "yclid", "msclkid"}
_TRACKING_PREFIXES = ("utm_",)


def _is_tracking(key: str) -> bool:
    k = key.lower()
    if k in _TRACKING_PARAMS_EXACT:
        return True
    return any(k.startswith(p) for p in _TRACKING_PREFIXES)


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    query_pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _is_tracking(k)]
    query = urlencode(sorted(query_pairs))
    return urlunsplit((parts.scheme, host, path, query, ""))


def _norm_title(title: str) -> str:
    return title.strip().lower()


def dedup_exact(items: list[Item]) -> list[Item]:
    """Dedup by normalized URL or normalized title; keep higher-scored item."""
    if not items:
        return []

    # Build canonical keys; collapse into the best representative
    best: dict[str, Item] = {}
    url_to_key: dict[str, str] = {}
    title_to_key: dict[str, str] = {}

    for item in items:
        nurl = normalize_url(item.url)
        ntitle = _norm_title(item.title)

        key_from_url = url_to_key.get(nurl)
        key_from_title = title_to_key.get(ntitle)
        existing_key = key_from_url or key_from_title

        if existing_key is None:
            key = nurl  # use URL as canonical key
            best[key] = item
            url_to_key[nurl] = key
            title_to_key[ntitle] = key
        else:
            current = best[existing_key]
            if item.score > current.score:
                best[existing_key] = item
            # ensure both indices point at this key
            url_to_key[nurl] = existing_key
            title_to_key[ntitle] = existing_key

    return list(best.values())


def _recency_key(item: Item) -> tuple[int, datetime]:
    """Sort key: (has_date, published). Items with date come first when sorted desc."""
    if item.published is None:
        # Use min datetime so None items rank lowest in desc sort
        return (0, datetime.min.replace(tzinfo=timezone.utc))
    return (1, item.published)


def prefilter(items: list[Item], max_candidates: int) -> list[Item]:
    """Sort by recency desc (None last) and truncate to max_candidates."""
    sorted_items = sorted(items, key=_recency_key, reverse=True)
    return sorted_items[:max_candidates]


# ----------------------------------------------------------------------------
# Semantic dedup
# ----------------------------------------------------------------------------


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _cluster_best_score(items: list[Item]) -> Item:
    """Pick the best item from a cluster: highest score, then most recent."""
    def key(item: Item):
        return (item.score, _recency_key(item))
    return max(items, key=key)


def dedup_semantic(
    items: list[Item],
    threshold: float,
    embedding_model: str,
) -> list[Item]:
    """Group items by embedding similarity ≥ threshold; keep best per cluster.

    On embedding failure, returns input unchanged.
    """
    if len(items) <= 1:
        return list(items)

    texts = [f"{item.title} {item.summary[:200]}".strip() for item in items]
    vectors: list[np.ndarray] = []
    try:
        for start in range(0, len(texts), _EMBED_BATCH_LIMIT):
            batch = texts[start : start + _EMBED_BATCH_LIMIT]
            response = litellm.embedding(model=embedding_model, input=batch)
            vectors.extend(np.array(d["embedding"], dtype=float) for d in response.data)
    except Exception as e:
        log.warning("Semantic dedup embedding call failed (%s); skipping", e)
        return list(items)

    if len(vectors) != len(items):
        log.warning("Embedding count mismatch; skipping semantic dedup")
        return list(items)

    # Union-find clustering
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if _cosine(vectors[i], vectors[j]) >= threshold:
                union(i, j)

    clusters: dict[int, list[Item]] = {}
    for i, item in enumerate(items):
        root = find(i)
        clusters.setdefault(root, []).append(item)

    return [_cluster_best_score(cluster) for cluster in clusters.values()]
