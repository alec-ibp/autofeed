from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import dedup
from src.dedup import dedup_semantic
from src.models import Item


def make_item(title: str, url: str, score: int = 0) -> Item:
    return Item(
        source="S",
        source_type="rss",
        title=title,
        url=url,
        published=datetime(2026, 5, 20, tzinfo=timezone.utc),
        score=score,
        summary="",
    )


def fake_embedding_response(vectors: list[list[float]]):
    return SimpleNamespace(data=[{"embedding": v} for v in vectors])


class TestDedupSemantic:
    def test_collapses_near_duplicates_keeping_higher_score(self, monkeypatch):
        # Items 0 and 1 have nearly identical vectors → should collapse
        # Item 2 has orthogonal vector → stays
        items = [
            make_item("Llama 4 released", "https://a.com/1", score=10),
            make_item("Llama 4 model released today", "https://b.com/2", score=100),
            make_item("Totally different topic", "https://c.com/3", score=50),
        ]
        # Vectors: items 0 and 1 are very similar (cos=1.0); item 2 is orthogonal
        vectors = [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]
        monkeypatch.setattr(
            dedup.litellm, "embedding",
            lambda **_: fake_embedding_response(vectors),
        )

        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        assert len(result) == 2
        # Higher-scored survivor from the duplicate cluster
        urls = {i.url for i in result}
        assert "https://b.com/2" in urls
        assert "https://c.com/3" in urls
        assert "https://a.com/1" not in urls

    def test_does_not_collapse_below_threshold(self, monkeypatch):
        items = [make_item("A", "https://a.com/1"), make_item("B", "https://b.com/2")]
        # Cosine ≈ 0.71 — below 0.85 threshold
        vectors = [[1.0, 0.0], [1.0, 1.0]]
        monkeypatch.setattr(
            dedup.litellm, "embedding",
            lambda **_: fake_embedding_response(vectors),
        )
        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        assert len(result) == 2

    def test_returns_input_on_embedding_failure(self, monkeypatch):
        items = [make_item("A", "https://a.com/1"), make_item("B", "https://b.com/2")]

        def boom(**_):
            raise RuntimeError("embedding api down")

        monkeypatch.setattr(dedup.litellm, "embedding", boom)
        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        assert len(result) == 2  # unchanged

    def test_empty_input(self):
        assert dedup_semantic([], threshold=0.85, embedding_model="m") == []

    def test_single_item_skips_embedding_call(self, monkeypatch):
        called = {"n": 0}

        def mock_emb(**_):
            called["n"] += 1
            return fake_embedding_response([[1.0]])

        monkeypatch.setattr(dedup.litellm, "embedding", mock_emb)
        items = [make_item("A", "https://a.com/1")]
        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        assert len(result) == 1
        assert called["n"] == 0  # short-circuited

    def test_chunks_embedding_calls_to_respect_provider_batch_limit(self, monkeypatch):
        """Gemini caps batch embeddings at 100 items. We must chunk."""
        n = 250
        items = [make_item(f"t{i}", f"https://ex.com/{i}", score=i) for i in range(n)]

        call_batch_sizes: list[int] = []

        def mock_emb(**kwargs):
            batch = kwargs.get("input")
            call_batch_sizes.append(len(batch))
            # Each vector orthogonal so no clustering
            vectors = [[1.0 if j == call_batch_sizes[-1] else 0.0] * 4 for j in range(len(batch))]
            # Build distinct unit vectors so cosine < threshold between any two
            vectors = [[float(idx == call_batch_sizes[-1] * 1000 + j) for idx in range(4)] for j in range(len(batch))]
            return fake_embedding_response(vectors)

        monkeypatch.setattr(dedup.litellm, "embedding", mock_emb)

        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        # Should split 250 into [100, 100, 50]
        assert call_batch_sizes == [100, 100, 50]
        # Total items returned equals the count (no clustering with orthogonal vectors)
        assert len(result) == n

    def test_failure_in_any_chunk_returns_input_unchanged(self, monkeypatch):
        n = 150
        items = [make_item(f"t{i}", f"https://ex.com/{i}") for i in range(n)]
        call_count = {"n": 0}

        def mock_emb(**_kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("rate limit")
            return fake_embedding_response([[1.0, 0.0]] * 100)

        monkeypatch.setattr(dedup.litellm, "embedding", mock_emb)
        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        # Degradation: returns input unchanged
        assert len(result) == n

    def test_three_way_chain_collapses_to_one(self, monkeypatch):
        # All three items vectors are similar → one cluster, highest score wins
        items = [
            make_item("v1", "https://a.com/1", score=5),
            make_item("v2", "https://b.com/2", score=20),
            make_item("v3", "https://c.com/3", score=10),
        ]
        vectors = [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
        monkeypatch.setattr(
            dedup.litellm, "embedding",
            lambda **_: fake_embedding_response(vectors),
        )
        result = dedup_semantic(items, threshold=0.85, embedding_model="m")
        assert len(result) == 1
        assert result[0].url == "https://b.com/2"
