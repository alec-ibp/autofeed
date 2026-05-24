from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import ranker
from src.models import Item
from src.ranker import rank_llm


def make_item(title: str, url: str, score: int = 0, source: str = "S") -> Item:
    return Item(
        source=source,
        source_type="rss",
        title=title,
        url=url,
        published=datetime(2026, 5, 20, tzinfo=timezone.utc),
        score=score,
        summary="",
    )


def fake_completion(content: str):
    """Build a LiteLLM-like response object."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


PROFILE = {
    "priorities": ["agents", "papers"],
    "anti_priorities": ["hype"],
    "exclude_keywords": [],
    "extra_notes": "senior engineer",
}

SETTINGS = {
    "model": "gemini/gemini-2.5-flash",
    "top_n": 2,
    "temperature": 0.3,
    "ranking_strategy": "single",
    "two_pass_threshold": 50,
}


class TestRankSinglePass:
    def test_parses_valid_json_response(self, monkeypatch):
        response = fake_completion(
            json.dumps(
                {
                    "items": [
                        {"rank": 1, "title": "A", "url": "https://a.com/x", "source": "S", "category": "Paper", "why": "Useful."},
                        {"rank": 2, "title": "B", "url": "https://b.com/y", "source": "S", "category": "Releases", "why": "Relevant."},
                    ]
                }
            )
        )
        monkeypatch.setattr(ranker.litellm, "completion", lambda **_: response)

        items = [make_item("A", "https://a.com/x"), make_item("B", "https://b.com/y")]
        ranked = rank_llm(items, PROFILE, SETTINGS)

        assert len(ranked) == 2
        assert ranked[0].title == "A"
        assert ranked[0].category == "Paper"
        assert ranked[0].why == "Useful."

    def test_strips_markdown_fences(self, monkeypatch):
        response = fake_completion(
            "```json\n"
            + json.dumps(
                {"items": [{"rank": 1, "title": "A", "url": "https://a.com/x", "source": "S", "category": "Paper", "why": "w"}]}
            )
            + "\n```"
        )
        monkeypatch.setattr(ranker.litellm, "completion", lambda **_: response)
        items = [make_item("A", "https://a.com/x")]
        ranked = rank_llm(items, PROFILE, SETTINGS)
        assert len(ranked) == 1

    def test_discards_items_with_url_not_in_candidates(self, monkeypatch):
        response = fake_completion(
            json.dumps(
                {
                    "items": [
                        {"rank": 1, "title": "A", "url": "https://a.com/x", "source": "S", "category": "Paper", "why": "w"},
                        {"rank": 2, "title": "Hallucinated", "url": "https://hallucinated.com/", "source": "S", "category": "Otro", "why": "w"},
                    ]
                }
            )
        )
        monkeypatch.setattr(ranker.litellm, "completion", lambda **_: response)
        items = [make_item("A", "https://a.com/x")]
        ranked = rank_llm(items, PROFILE, SETTINGS)
        assert len(ranked) == 1
        assert ranked[0].title == "A"

    def test_falls_back_to_heuristic_on_invalid_json_after_retry(self, monkeypatch):
        calls = {"count": 0}

        def bad_completion(**_kwargs):
            calls["count"] += 1
            return fake_completion("not json at all")

        monkeypatch.setattr(ranker.litellm, "completion", bad_completion)

        items = [
            make_item("Low", "https://l.com/x", score=1),
            make_item("High", "https://h.com/y", score=100),
        ]
        ranked = rank_llm(items, PROFILE, SETTINGS)
        # Heuristic fallback orders by score desc
        assert ranked[0].title == "High"
        # Verify retry: called 2 times (initial + 1 retry) before fallback
        assert calls["count"] == 2

    def test_falls_back_on_litellm_exception(self, monkeypatch):
        def boom(**_kwargs):
            raise RuntimeError("rate limit")

        monkeypatch.setattr(ranker.litellm, "completion", boom)
        items = [make_item("Only", "https://only.com/x", score=1)]
        ranked = rank_llm(items, PROFILE, SETTINGS)
        assert len(ranked) == 1
        assert ranked[0].title == "Only"

    def test_caps_at_top_n_even_if_llm_returns_more(self, monkeypatch):
        response = fake_completion(
            json.dumps(
                {
                    "items": [
                        {"rank": i + 1, "title": f"i{i}", "url": f"https://x.com/{i}", "source": "S", "category": "Otro", "why": "w"}
                        for i in range(5)
                    ]
                }
            )
        )
        monkeypatch.setattr(ranker.litellm, "completion", lambda **_: response)
        items = [make_item(f"i{i}", f"https://x.com/{i}") for i in range(5)]
        settings = {**SETTINGS, "top_n": 2}
        ranked = rank_llm(items, PROFILE, settings)
        assert len(ranked) == 2

    def test_prompt_includes_structured_profile(self, monkeypatch):
        captured: dict = {}

        def capture(**kwargs):
            captured["messages"] = kwargs.get("messages")
            captured["model"] = kwargs.get("model")
            return fake_completion(json.dumps({"items": []}))

        monkeypatch.setattr(ranker.litellm, "completion", capture)
        items = [make_item("A", "https://a.com/x")]
        rank_llm(items, PROFILE, SETTINGS)

        prompt_text = "\n".join(m["content"] for m in captured["messages"])
        assert "PRIORITIES" in prompt_text
        assert "agents" in prompt_text
        assert "ANTI-PRIORITIES" in prompt_text
        assert "hype" in prompt_text
        assert captured["model"] == "gemini/gemini-2.5-flash"
