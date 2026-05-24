from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.memory import filter_unseen, load_seen_urls, persist_week
from src.models import Item


def make_item(title: str = "t", url: str = "https://ex.com/a", score: int = 0) -> Item:
    return Item(
        source="S",
        source_type="rss",
        title=title,
        url=url,
        published=None,
        score=score,
        summary="",
    )


# ----------------------------------------------------------------------------
# load_seen_urls
# ----------------------------------------------------------------------------


class TestLoadSeenUrls:
    def test_returns_empty_when_file_missing(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        assert load_seen_urls(path, memory_weeks=4) == set()

    def test_returns_empty_when_file_corrupt(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        path.write_text("{not json")
        assert load_seen_urls(path, memory_weeks=4) == set()

    def test_returns_normalized_urls_from_last_n_weeks(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        payload = {
            "2026-W21": {
                "generated_at": "2026-05-23T00:00:00Z",
                "urls": ["https://a.com/x", "https://b.com/y"],
            },
            "2026-W20": {
                "generated_at": "2026-05-16T00:00:00Z",
                "urls": ["https://c.com/z"],
            },
            "2026-W10": {
                "generated_at": "2026-03-07T00:00:00Z",
                "urls": ["https://old.com/forever-ago"],
            },
        }
        path.write_text(json.dumps(payload))

        # memory_weeks=2 should return only the 2 most recent weeks' URLs
        result = load_seen_urls(path, memory_weeks=2)
        assert result == {"https://a.com/x", "https://b.com/y", "https://c.com/z"}


# ----------------------------------------------------------------------------
# filter_unseen
# ----------------------------------------------------------------------------


class TestFilterUnseen:
    def test_excludes_items_whose_url_is_seen(self):
        items = [
            make_item(url="https://a.com/seen"),
            make_item(url="https://b.com/fresh"),
        ]
        seen = {"https://a.com/seen"}
        result = filter_unseen(items, seen)
        assert len(result) == 1
        assert result[0].url == "https://b.com/fresh"

    def test_normalizes_url_before_comparing(self):
        items = [make_item(url="https://A.com/Path/?utm_source=x")]
        seen = {"https://a.com/Path"}  # what normalize_url produces
        result = filter_unseen(items, seen)
        assert result == []

    def test_empty_seen_passes_all_through(self):
        items = [make_item(url="https://a.com/x"), make_item(url="https://b.com/y")]
        assert len(filter_unseen(items, set())) == 2


# ----------------------------------------------------------------------------
# persist_week
# ----------------------------------------------------------------------------


class TestPersistWeek:
    def test_creates_file_when_missing(self, tmp_path: Path):
        path = tmp_path / "state" / "seen.json"  # nested dir on purpose
        items = [make_item(url="https://a.com/x")]
        persist_week(path, week_label="2026-W21", items=items, memory_weeks=4)
        assert path.exists()
        data = json.loads(path.read_text())
        assert "2026-W21" in data
        assert data["2026-W21"]["urls"] == ["https://a.com/x"]

    def test_normalizes_urls_on_write(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        items = [make_item(url="https://A.com/Post/?utm_source=x#frag")]
        persist_week(path, week_label="2026-W21", items=items, memory_weeks=4)
        data = json.loads(path.read_text())
        assert data["2026-W21"]["urls"] == ["https://a.com/Post"]

    def test_overwrites_same_week(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        persist_week(path, "2026-W21", [make_item(url="https://a.com/old")], memory_weeks=4)
        persist_week(path, "2026-W21", [make_item(url="https://a.com/new")], memory_weeks=4)
        data = json.loads(path.read_text())
        assert data["2026-W21"]["urls"] == ["https://a.com/new"]

    def test_truncates_to_memory_weeks(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        # Write 5 weeks, then assert only the most recent 3 remain
        for label in ["2026-W17", "2026-W18", "2026-W19", "2026-W20", "2026-W21"]:
            persist_week(path, label, [make_item(url=f"https://x.com/{label}")], memory_weeks=3)
        data = json.loads(path.read_text())
        assert set(data.keys()) == {"2026-W19", "2026-W20", "2026-W21"}

    def test_writes_valid_json_with_generated_at(self, tmp_path: Path):
        path = tmp_path / "seen.json"
        persist_week(path, "2026-W21", [make_item(url="https://a.com/x")], memory_weeks=4)
        data = json.loads(path.read_text())
        entry = data["2026-W21"]
        assert "generated_at" in entry
        # Round-trip parse to ensure ISO format
        datetime.fromisoformat(entry["generated_at"].replace("Z", "+00:00"))
