from __future__ import annotations

from datetime import datetime, timezone

from src.models import Item
from src.ranker import rank_heuristic


def make_item(title: str, score: int, published: datetime | None = None) -> Item:
    return Item(
        source="S",
        source_type="rss",
        title=title,
        url=f"https://ex.com/{title}",
        published=published,
        score=score,
        summary="",
    )


def test_orders_by_score_desc():
    items = [make_item("low", 1), make_item("high", 100), make_item("mid", 50)]
    result = rank_heuristic(items, top_n=3)
    assert [r.title for r in result] == ["high", "mid", "low"]
    assert [r.rank for r in result] == [1, 2, 3]


def test_truncates_to_top_n():
    items = [make_item(str(i), score=i) for i in range(10)]
    result = rank_heuristic(items, top_n=3)
    assert len(result) == 3


def test_recency_breaks_score_ties():
    d = lambda y, m, day: datetime(y, m, day, tzinfo=timezone.utc)  # noqa: E731
    items = [
        make_item("older", score=10, published=d(2026, 1, 1)),
        make_item("newer", score=10, published=d(2026, 5, 1)),
    ]
    result = rank_heuristic(items, top_n=2)
    assert result[0].title == "newer"


def test_assigns_default_category_and_empty_why():
    result = rank_heuristic([make_item("x", 1)], top_n=1)
    assert result[0].category == "Otro"
    assert result[0].why == ""


def test_handles_empty_input():
    assert rank_heuristic([], top_n=10) == []
