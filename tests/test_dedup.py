from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.dedup import dedup_exact, normalize_url, prefilter
from src.models import Item


def make_item(
    title: str = "t",
    url: str = "https://example.com/a",
    score: int = 0,
    published: datetime | None = None,
    source: str = "S",
    source_type: str = "rss",
    summary: str = "",
) -> Item:
    return Item(
        source=source,
        source_type=source_type,
        title=title,
        url=url,
        published=published,
        score=score,
        summary=summary,
    )


# ----------------------------------------------------------------------------
# normalize_url
# ----------------------------------------------------------------------------


class TestNormalizeUrl:
    def test_strips_utm_params(self):
        assert (
            normalize_url("https://example.com/post?utm_source=twitter&utm_medium=social")
            == "https://example.com/post"
        )

    def test_strips_other_tracking_params(self):
        url = "https://example.com/post?ref=hn&fbclid=abc&gclid=xyz"
        assert normalize_url(url) == "https://example.com/post"

    def test_preserves_non_tracking_params(self):
        url = "https://example.com/post?id=42&page=2"
        # Both id and page are not tracking; should be preserved (order-insensitive)
        result = normalize_url(url)
        assert result.startswith("https://example.com/post?")
        assert "id=42" in result
        assert "page=2" in result

    def test_lowercases_host_only(self):
        assert (
            normalize_url("https://Example.COM/MyPost")
            == "https://example.com/MyPost"
        )

    def test_strips_trailing_slash(self):
        assert normalize_url("https://example.com/post/") == "https://example.com/post"

    def test_strips_fragment(self):
        assert (
            normalize_url("https://example.com/post#section-1")
            == "https://example.com/post"
        )

    def test_handles_clean_url(self):
        assert normalize_url("https://example.com/post") == "https://example.com/post"

    def test_handles_root_url(self):
        assert normalize_url("https://example.com/") == "https://example.com"


# ----------------------------------------------------------------------------
# dedup_exact
# ----------------------------------------------------------------------------


class TestDedupExact:
    def test_same_url_collapses_keeping_higher_score(self):
        a = make_item(title="A", url="https://ex.com/p?utm_source=x", score=5)
        b = make_item(title="A2", url="https://ex.com/p", score=10)
        result = dedup_exact([a, b])
        assert len(result) == 1
        assert result[0].score == 10

    def test_same_normalized_title_collapses(self):
        a = make_item(title="Same Title", url="https://a.com/x", score=5)
        b = make_item(title="  same title  ", url="https://b.com/y", score=10)
        result = dedup_exact([a, b])
        assert len(result) == 1
        assert result[0].score == 10

    def test_distinct_items_pass_through(self):
        a = make_item(title="A", url="https://ex.com/a")
        b = make_item(title="B", url="https://ex.com/b")
        result = dedup_exact([a, b])
        assert len(result) == 2

    def test_empty_list(self):
        assert dedup_exact([]) == []


# ----------------------------------------------------------------------------
# prefilter
# ----------------------------------------------------------------------------


class TestPrefilter:
    def test_under_max_returns_all(self):
        items = [make_item(title=str(i)) for i in range(3)]
        assert len(prefilter(items, 10)) == 3

    def test_over_max_returns_max_sorted_by_recency(self):
        d = lambda y, m, day: datetime(y, m, day, tzinfo=timezone.utc)  # noqa: E731
        items = [
            make_item(title="old", published=d(2024, 1, 1)),
            make_item(title="newest", published=d(2024, 6, 1)),
            make_item(title="mid", published=d(2024, 3, 1)),
        ]
        result = prefilter(items, 2)
        assert len(result) == 2
        assert result[0].title == "newest"
        assert result[1].title == "mid"

    def test_none_published_goes_last(self):
        d = lambda y, m, day: datetime(y, m, day, tzinfo=timezone.utc)  # noqa: E731
        items = [
            make_item(title="no_date", published=None),
            make_item(title="dated", published=d(2024, 6, 1)),
        ]
        result = prefilter(items, 5)
        assert result[0].title == "dated"
        assert result[1].title == "no_date"
