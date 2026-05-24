from __future__ import annotations

from datetime import datetime, timezone

from src.models import RankedItem
from src.render import render_markdown


def test_renders_full_digest_with_two_items():
    ranked = [
        RankedItem(
            rank=1,
            title="Paper sobre agentes",
            url="https://arxiv.org/abs/2401.0001",
            source="arXiv:cs.AI",
            category="Paper",
            why="Propone un benchmark reproducible para agentes con tool use.",
        ),
        RankedItem(
            rank=2,
            title="langgraph v0.3 release",
            url="https://github.com/langchain-ai/langgraph/releases/tag/v0.3",
            source="GitHub:langchain-ai/langgraph",
            category="Releases",
            why="Streaming nativo de tool calls; impacta directamente cómo se estructuran loops.",
        ),
    ]
    generated_at = datetime(2026, 5, 23, 23, 0, 0, tzinfo=timezone.utc)
    sources_summary = {"rss": 12, "reddit": 5, "hn": 8, "arxiv": 30, "hf_papers": 4, "github": 3}

    md = render_markdown(
        ranked=ranked,
        week_label="2026-W21",
        generated_at=generated_at,
        model="gemini/gemini-2.5-flash",
        sources_summary=sources_summary,
    )

    # Header
    assert "# AI Digest — Semana 2026-W21" in md
    assert "gemini/gemini-2.5-flash" in md
    assert "2026-05-23" in md
    # Items: each appears with rank, title, source, category, why, link
    assert "## 1. Paper sobre agentes" in md
    assert "arXiv:cs.AI" in md
    assert "Paper" in md
    assert "benchmark reproducible" in md
    assert "[Leer](https://arxiv.org/abs/2401.0001)" in md
    assert "## 2. langgraph v0.3 release" in md
    assert "[Leer](https://github.com/langchain-ai/langgraph/releases/tag/v0.3)" in md
    # Footer with counts
    assert "2 items" in md
    # All source counts surface
    for label in ("rss", "reddit", "hn", "arxiv", "hf_papers", "github"):
        assert label in md


def test_renders_empty_state_when_no_items():
    md = render_markdown(
        ranked=[],
        week_label="2026-W21",
        generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        model="gemini/gemini-2.5-flash",
        sources_summary={"rss": 0},
    )
    assert "# AI Digest — Semana 2026-W21" in md
    assert "sin items relevantes" in md.lower()


def test_renders_separator_between_items():
    ranked = [
        RankedItem(rank=1, title="A", url="https://a.com", source="X", category="Y", why="w1"),
        RankedItem(rank=2, title="B", url="https://b.com", source="X", category="Y", why="w2"),
    ]
    md = render_markdown(
        ranked=ranked,
        week_label="2026-W21",
        generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        model="m",
        sources_summary={},
    )
    # Item 1 must appear before item 2
    assert md.index("## 1.") < md.index("## 2.")
