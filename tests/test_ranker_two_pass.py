from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import ranker
from src.models import Item
from src.ranker import rank_llm


def make_item(idx: int, score: int = 0) -> Item:
    return Item(
        source="S",
        source_type="rss",
        title=f"Item {idx}",
        url=f"https://ex.com/{idx}",
        published=datetime(2026, 5, 20, tzinfo=timezone.utc),
        score=score,
        summary="",
    )


def fake_completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


PROFILE = {"priorities": ["a"], "anti_priorities": ["b"], "exclude_keywords": [], "extra_notes": ""}


def _final_response(urls: list[str]) -> str:
    return json.dumps(
        {
            "items": [
                {"rank": i + 1, "title": f"T{i}", "url": url, "source": "S", "category": "Otro", "why": "w"}
                for i, url in enumerate(urls)
            ]
        }
    )


def _classification_response(results: list[dict]) -> str:
    return json.dumps({"results": results})


class TestTwoPassRanking:
    def test_two_pass_runs_when_strategy_is_two_pass(self, monkeypatch):
        # 5 items, batch_size set to 3 → 2 batches + 1 fine pass = 3 calls
        items = [make_item(i) for i in range(5)]
        # Batch 1 (idx 0,1,2): keep 0, maybe 1, discard 2
        batch1 = _classification_response(
            [{"idx": 0, "label": "keep"}, {"idx": 1, "label": "maybe"}, {"idx": 2, "label": "discard"}]
        )
        # Batch 2 (idx 0,1): keep both
        batch2 = _classification_response(
            [{"idx": 0, "label": "keep"}, {"idx": 1, "label": "keep"}]
        )
        # Fine pass over 4 survivors (items 0, 1, 3, 4) — return top 2
        final = _final_response(["https://ex.com/0", "https://ex.com/3"])

        call_log = [batch1, batch2, final]
        idx_box = {"i": 0}

        def mock_completion(**_kwargs):
            r = call_log[idx_box["i"]]
            idx_box["i"] += 1
            return fake_completion(r)

        monkeypatch.setattr(ranker.litellm, "completion", mock_completion)
        monkeypatch.setattr(ranker, "_ROUGH_BATCH_SIZE", 3)

        settings = {
            "model": "m",
            "top_n": 2,
            "temperature": 0.0,
            "ranking_strategy": "two-pass",
            "two_pass_threshold": 1,  # force two-pass
        }
        result = rank_llm(items, PROFILE, settings)

        assert len(result) == 2
        assert {r.url for r in result} == {"https://ex.com/0", "https://ex.com/3"}
        assert idx_box["i"] == 3  # 2 batch calls + 1 fine pass

    def test_auto_uses_single_pass_under_threshold(self, monkeypatch):
        items = [make_item(i) for i in range(5)]
        calls = {"n": 0}

        def mock_completion(**_kwargs):
            calls["n"] += 1
            return fake_completion(_final_response(["https://ex.com/0", "https://ex.com/1"]))

        monkeypatch.setattr(ranker.litellm, "completion", mock_completion)
        settings = {
            "model": "m",
            "top_n": 2,
            "temperature": 0.0,
            "ranking_strategy": "auto",
            "two_pass_threshold": 50,  # 5 items < 50 → single-pass
        }
        rank_llm(items, PROFILE, settings)
        assert calls["n"] == 1  # only the fine pass, no rough pass

    def test_auto_uses_two_pass_over_threshold(self, monkeypatch):
        items = [make_item(i) for i in range(8)]
        # Will batch into ceil(8/3) = 3 rough batches + 1 final = 4 calls
        call_log: list[str] = []
        # Rough passes: keep everyone
        for _ in range(3):
            call_log.append(
                _classification_response([{"idx": j, "label": "keep"} for j in range(3)])
            )
        # Final pass returns first 2 URLs
        call_log.append(_final_response(["https://ex.com/0", "https://ex.com/1"]))

        idx_box = {"i": 0}

        def mock_completion(**_kwargs):
            r = call_log[idx_box["i"]]
            idx_box["i"] += 1
            return fake_completion(r)

        monkeypatch.setattr(ranker.litellm, "completion", mock_completion)
        monkeypatch.setattr(ranker, "_ROUGH_BATCH_SIZE", 3)

        settings = {
            "model": "m",
            "top_n": 2,
            "temperature": 0.0,
            "ranking_strategy": "auto",
            "two_pass_threshold": 5,  # 8 > 5 → two-pass
        }
        result = rank_llm(items, PROFILE, settings)
        assert len(result) == 2
        assert idx_box["i"] == 4

    def test_failed_batch_passes_all_as_maybe(self, monkeypatch):
        items = [make_item(i) for i in range(4)]
        # Batch 1 (idx 0,1) succeeds with discards; Batch 2 (idx 0,1) FAILS
        idx_box = {"i": 0}

        def mock_completion(**_kwargs):
            idx_box["i"] += 1
            if idx_box["i"] == 1:
                return fake_completion(
                    _classification_response(
                        [{"idx": 0, "label": "discard"}, {"idx": 1, "label": "discard"}]
                    )
                )
            if idx_box["i"] == 2:
                raise RuntimeError("rate limit on batch 2")
            # Fine pass: assume both items 2 and 3 (from failed batch) survive → return them
            return fake_completion(
                _final_response(["https://ex.com/2", "https://ex.com/3"])
            )

        monkeypatch.setattr(ranker.litellm, "completion", mock_completion)
        monkeypatch.setattr(ranker, "_ROUGH_BATCH_SIZE", 2)

        settings = {
            "model": "m",
            "top_n": 2,
            "temperature": 0.0,
            "ranking_strategy": "two-pass",
            "two_pass_threshold": 1,
        }
        result = rank_llm(items, PROFILE, settings)
        # Items from failed batch survive into the fine pass and end up in the result
        assert {r.url for r in result} == {"https://ex.com/2", "https://ex.com/3"}
