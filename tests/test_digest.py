from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.digest import expand_env, run_digest
from src.models import Item


def make_item(title: str, url: str, score: int = 0, source: str = "Test") -> Item:
    return Item(
        source=source,
        source_type="rss",
        title=title,
        url=url,
        published=datetime(2026, 5, 20, tzinfo=timezone.utc),
        score=score,
        summary="",
    )


MINIMAL_CONFIG = {
    "profile": {"priorities": [], "anti_priorities": [], "exclude_keywords": [], "extra_notes": ""},
    "settings": {
        "model": "gemini/gemini-2.5-flash",
        "embedding_model": "gemini/text-embedding-004",
        "top_n": 5,
        "days_back": 7,
        "max_candidates": 50,
        "temperature": 0.3,
        "ranking_strategy": "single",
        "two_pass_threshold": 50,
        "semantic_dedup": False,  # skip embeddings in this test
        "semantic_dedup_threshold": 0.85,
        "memory_weeks": 4,
    },
    "rss": [],
    "reddit": {"subs": [], "min_score": 0, "limit_per_sub": 0},
    "hackernews": {"queries": [], "min_points": 0, "hits_per_query": 0},
    "arxiv": {"categories": [], "max_per_category": 0},
    "hf_papers": {"days_back": 7, "min_upvotes": 0},
    "github": {"repos": [], "include_prereleases": False},
    "delivery": {"commit": True, "email": {"enabled": False}},
}


def test_end_to_end_heuristic(monkeypatch, tmp_path: Path):
    # All collectors return predetermined items
    monkeypatch.setattr(
        "src.digest.fetch_rss",
        lambda feeds, since: [make_item("RSS post", "https://blog.com/rss-post", score=0)],
    )
    monkeypatch.setattr(
        "src.digest.fetch_reddit",
        lambda cfg, since: [make_item("Reddit top", "https://r.com/x", score=200, source="r/X")],
    )
    monkeypatch.setattr(
        "src.digest.fetch_hn",
        lambda cfg, since: [make_item("HN story", "https://news.ycombinator.com/item?id=1", score=150)],
    )
    monkeypatch.setattr("src.digest.fetch_arxiv", lambda cfg, since: [])
    monkeypatch.setattr("src.digest.fetch_hf_papers", lambda cfg, since: [])
    monkeypatch.setattr("src.digest.fetch_github", lambda cfg, since: [])

    now = datetime(2026, 5, 23, 23, 0, 0, tzinfo=timezone.utc)
    # use_llm=False forces heuristic path
    digest_path = run_digest(MINIMAL_CONFIG, base_dir=tmp_path, now=now, use_llm=False)

    # File exists at expected path
    iso = now.isocalendar()
    expected_name = f"{iso.year}-W{iso.week:02d}.md"
    assert digest_path.name == expected_name
    assert digest_path.exists()

    content = digest_path.read_text()
    assert "# AI Digest — Semana" in content
    # Reddit top should be #1 (highest score)
    assert content.index("Reddit top") < content.index("HN story")

    # Memory file should be created with the 3 URLs
    state_path = tmp_path / "digests" / "state" / "seen_urls.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text())
    week_label = f"{iso.year}-W{iso.week:02d}"
    assert week_label in state
    assert len(state[week_label]["urls"]) == 3


def test_empty_collectors_produces_empty_state_digest(monkeypatch, tmp_path: Path):
    for name in ("fetch_rss", "fetch_reddit", "fetch_hn", "fetch_arxiv", "fetch_hf_papers", "fetch_github"):
        monkeypatch.setattr(f"src.digest.{name}", lambda *_args, **_kwargs: [])
    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    digest_path = run_digest(MINIMAL_CONFIG, base_dir=tmp_path, now=now, use_llm=False)
    content = digest_path.read_text()
    assert "sin items relevantes" in content.lower()


class TestExpandEnv:
    def test_substitutes_single_placeholder(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert expand_env("${FOO}") == "bar"

    def test_substitutes_inside_text(self, monkeypatch):
        monkeypatch.setenv("WHO", "world")
        assert expand_env("hello ${WHO}!") == "hello world!"

    def test_missing_var_becomes_empty_string(self, monkeypatch):
        monkeypatch.delenv("ABSENT", raising=False)
        assert expand_env("${ABSENT}") == ""

    def test_passthrough_for_non_placeholder_strings(self):
        assert expand_env("plain text $100") == "plain text $100"

    def test_recurses_into_dicts_and_lists(self, monkeypatch):
        monkeypatch.setenv("X", "value")
        data = {"a": "${X}", "b": ["${X}", "lit", 42], "c": {"nested": "${X}"}}
        assert expand_env(data) == {
            "a": "value",
            "b": ["value", "lit", 42],
            "c": {"nested": "value"},
        }

    def test_non_string_values_pass_through(self):
        assert expand_env(42) == 42
        assert expand_env(True) is True
        assert expand_env(None) is None


def test_memory_excludes_seen_urls_across_weeks(monkeypatch, tmp_path: Path):
    # Pre-seed memory with a URL that one collector will return
    state_path = tmp_path / "digests" / "state" / "seen_urls.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "2026-W20": {
                    "generated_at": "2026-05-16T00:00:00Z",
                    "urls": ["https://blog.com/seen"],
                }
            }
        )
    )

    monkeypatch.setattr(
        "src.digest.fetch_rss",
        lambda feeds, since: [
            make_item("Already seen", "https://blog.com/seen", score=10),
            make_item("Fresh", "https://blog.com/fresh", score=5),
        ],
    )
    for name in ("fetch_reddit", "fetch_hn", "fetch_arxiv", "fetch_hf_papers", "fetch_github"):
        monkeypatch.setattr(f"src.digest.{name}", lambda *_a, **_k: [])

    now = datetime(2026, 5, 23, tzinfo=timezone.utc)
    digest_path = run_digest(MINIMAL_CONFIG, base_dir=tmp_path, now=now, use_llm=False)
    content = digest_path.read_text()
    assert "Already seen" not in content
    assert "Fresh" in content
