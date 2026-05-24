from __future__ import annotations

import time
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import responses

from src import sources
from src.sources import (
    fetch_arxiv,
    fetch_github,
    fetch_hf_papers,
    fetch_hn,
    fetch_reddit,
    fetch_rss,
)


# ============================================================================
# fetch_rss
# ============================================================================


class TestFetchRss:
    def test_parses_feed_entries(self, monkeypatch):
        fake = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="Article 1",
                    link="https://blog.com/a1",
                    published_parsed=time.struct_time((2026, 5, 20, 10, 0, 0, 0, 0, 0)),
                    summary="An interesting post about agents",
                )
            ],
            bozo=0,
        )
        monkeypatch.setattr(sources.feedparser, "parse", lambda url: fake)

        feeds = [{"name": "Test Blog", "url": "https://blog.com/feed"}]
        since = datetime(2026, 5, 1, tzinfo=timezone.utc)

        items = fetch_rss(feeds, since)

        assert len(items) == 1
        assert items[0].title == "Article 1"
        assert items[0].url == "https://blog.com/a1"
        assert items[0].source == "Test Blog"
        assert items[0].source_type == "rss"
        assert items[0].score == 0
        assert items[0].published is not None
        assert items[0].published.tzinfo is not None

    def test_excludes_entries_older_than_since(self, monkeypatch):
        fake = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="Old",
                    link="https://b.com/old",
                    published_parsed=time.struct_time((2025, 1, 1, 0, 0, 0, 0, 0, 0)),
                    summary="",
                ),
                SimpleNamespace(
                    title="Fresh",
                    link="https://b.com/fresh",
                    published_parsed=time.struct_time((2026, 5, 20, 0, 0, 0, 0, 0, 0)),
                    summary="",
                ),
            ],
            bozo=0,
        )
        monkeypatch.setattr(sources.feedparser, "parse", lambda url: fake)

        since = datetime(2026, 5, 1, tzinfo=timezone.utc)
        items = fetch_rss([{"name": "B", "url": "u"}], since)
        assert [i.title for i in items] == ["Fresh"]

    def test_entries_without_date_pass_filter(self, monkeypatch):
        fake = SimpleNamespace(
            entries=[
                SimpleNamespace(title="NoDate", link="https://b.com/n", summary=""),
            ],
            bozo=0,
        )
        monkeypatch.setattr(sources.feedparser, "parse", lambda url: fake)

        since = datetime(2026, 5, 1, tzinfo=timezone.utc)
        items = fetch_rss([{"name": "B", "url": "u"}], since)
        assert len(items) == 1
        assert items[0].published is None

    def test_failure_returns_empty_does_not_raise(self, monkeypatch):
        def boom(url):
            raise RuntimeError("network down")

        monkeypatch.setattr(sources.feedparser, "parse", boom)
        assert fetch_rss([{"name": "B", "url": "u"}], datetime(2026, 5, 1, tzinfo=timezone.utc)) == []

    def test_summary_html_stripped_and_truncated(self, monkeypatch):
        long_html = "<p>" + "x" * 1000 + "</p>"
        fake = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="t",
                    link="https://b.com/x",
                    published_parsed=time.struct_time((2026, 5, 20, 0, 0, 0, 0, 0, 0)),
                    summary=long_html,
                )
            ],
            bozo=0,
        )
        monkeypatch.setattr(sources.feedparser, "parse", lambda url: fake)
        items = fetch_rss([{"name": "B", "url": "u"}], datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert "<p>" not in items[0].summary
        assert len(items[0].summary) <= 500


# ============================================================================
# fetch_reddit
# ============================================================================


class TestFetchReddit:
    @responses.activate
    def test_extracts_posts_above_min_score(self):
        responses.add(
            responses.GET,
            "https://www.reddit.com/r/LocalLLaMA/top.json",
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Hot post",
                                "url": "https://example.com/hot",
                                "permalink": "/r/LocalLLaMA/comments/abc/hot/",
                                "ups": 200,
                                "created_utc": 1747958400,  # 2025-05-23
                                "selftext": "body text",
                                "is_self": False,
                            }
                        },
                        {
                            "data": {
                                "title": "Cold post",
                                "url": "https://example.com/cold",
                                "permalink": "/r/LocalLLaMA/comments/xyz/cold/",
                                "ups": 5,
                                "created_utc": 1747958400,
                                "selftext": "",
                                "is_self": False,
                            }
                        },
                    ]
                }
            },
            status=200,
        )

        cfg = {"subs": ["LocalLLaMA"], "min_score": 50, "limit_per_sub": 25}
        since = datetime(2020, 1, 1, tzinfo=timezone.utc)
        items = fetch_reddit(cfg, since)

        assert len(items) == 1
        assert items[0].title == "Hot post"
        assert items[0].score == 200
        assert items[0].url == "https://example.com/hot"
        assert items[0].source == "r/LocalLLaMA"
        assert items[0].source_type == "reddit"

    @responses.activate
    def test_self_post_uses_permalink(self):
        responses.add(
            responses.GET,
            "https://www.reddit.com/r/LocalLLaMA/top.json",
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "Self post",
                                "url": "https://www.reddit.com/r/LocalLLaMA/comments/abc/self/",
                                "permalink": "/r/LocalLLaMA/comments/abc/self/",
                                "ups": 100,
                                "created_utc": 1747958400,
                                "selftext": "discussion content",
                                "is_self": True,
                            }
                        },
                    ]
                }
            },
            status=200,
        )
        cfg = {"subs": ["LocalLLaMA"], "min_score": 50, "limit_per_sub": 25}
        items = fetch_reddit(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert items[0].url == "https://www.reddit.com/r/LocalLLaMA/comments/abc/self/"

    @responses.activate
    def test_sends_user_agent_header(self):
        responses.add(
            responses.GET,
            "https://www.reddit.com/r/LocalLLaMA/top.json",
            json={"data": {"children": []}},
            status=200,
        )
        cfg = {"subs": ["LocalLLaMA"], "min_score": 50, "limit_per_sub": 25}
        fetch_reddit(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert "ai-digest" in responses.calls[0].request.headers["User-Agent"].lower()

    @responses.activate
    def test_429_returns_empty(self):
        responses.add(
            responses.GET,
            "https://www.reddit.com/r/LocalLLaMA/top.json",
            status=429,
        )
        cfg = {"subs": ["LocalLLaMA"], "min_score": 50, "limit_per_sub": 25}
        assert fetch_reddit(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc)) == []

    @responses.activate
    def test_one_sub_failure_doesnt_kill_others(self):
        responses.add(
            responses.GET,
            "https://www.reddit.com/r/A/top.json",
            status=500,
        )
        responses.add(
            responses.GET,
            "https://www.reddit.com/r/B/top.json",
            json={
                "data": {
                    "children": [
                        {
                            "data": {
                                "title": "From B",
                                "url": "https://b.com/x",
                                "permalink": "/r/B/comments/x/",
                                "ups": 100,
                                "created_utc": 1747958400,
                                "selftext": "",
                                "is_self": False,
                            }
                        }
                    ]
                }
            },
            status=200,
        )
        cfg = {"subs": ["A", "B"], "min_score": 50, "limit_per_sub": 25}
        items = fetch_reddit(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert len(items) == 1
        assert items[0].source == "r/B"


# ============================================================================
# fetch_hn
# ============================================================================


class TestFetchHn:
    @responses.activate
    def test_extracts_hits(self):
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={
                "hits": [
                    {
                        "objectID": "42",
                        "title": "Some agentic post",
                        "url": "https://blog.com/agent",
                        "points": 250,
                        "created_at": "2026-05-20T10:00:00Z",
                    }
                ]
            },
            status=200,
        )
        cfg = {"queries": ["agent"], "min_points": 50, "hits_per_query": 20}
        items = fetch_hn(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))

        assert len(items) == 1
        assert items[0].title == "Some agentic post"
        assert items[0].url == "https://blog.com/agent"
        assert items[0].score == 250
        assert items[0].source == "Hacker News"
        assert items[0].source_type == "hn"

    @responses.activate
    def test_null_url_falls_back_to_hn_permalink(self):
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={
                "hits": [
                    {
                        "objectID": "99",
                        "title": "Ask HN: ...",
                        "url": None,
                        "points": 80,
                        "created_at": "2026-05-20T00:00:00Z",
                    }
                ]
            },
            status=200,
        )
        cfg = {"queries": ["ask"], "min_points": 50, "hits_per_query": 20}
        items = fetch_hn(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert items[0].url == "https://news.ycombinator.com/item?id=99"

    @responses.activate
    def test_failure_returns_empty(self):
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            status=503,
        )
        cfg = {"queries": ["agent"], "min_points": 50, "hits_per_query": 20}
        assert fetch_hn(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc)) == []

    @responses.activate
    def test_deduplicates_hits_seen_in_multiple_queries(self):
        # Same objectID returned for two queries; should appear once
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={
                "hits": [
                    {
                        "objectID": "1",
                        "title": "Same item",
                        "url": "https://x.com",
                        "points": 100,
                        "created_at": "2026-05-20T00:00:00Z",
                    }
                ]
            },
            status=200,
        )
        responses.add(
            responses.GET,
            "https://hn.algolia.com/api/v1/search",
            json={
                "hits": [
                    {
                        "objectID": "1",
                        "title": "Same item",
                        "url": "https://x.com",
                        "points": 100,
                        "created_at": "2026-05-20T00:00:00Z",
                    }
                ]
            },
            status=200,
        )
        cfg = {"queries": ["q1", "q2"], "min_points": 50, "hits_per_query": 20}
        items = fetch_hn(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert len(items) == 1


# ============================================================================
# fetch_arxiv
# ============================================================================


class TestFetchArxiv:
    def test_parses_categories_into_items(self, monkeypatch):
        fake = SimpleNamespace(
            entries=[
                SimpleNamespace(
                    title="Paper A",
                    link="https://arxiv.org/abs/2401.0001",
                    published_parsed=time.struct_time((2026, 5, 20, 0, 0, 0, 0, 0, 0)),
                    summary="abstract A",
                ),
                SimpleNamespace(
                    title="Paper B",
                    link="https://arxiv.org/abs/2401.0002",
                    published_parsed=time.struct_time((2026, 5, 19, 0, 0, 0, 0, 0, 0)),
                    summary="abstract B",
                ),
            ],
            bozo=0,
        )
        monkeypatch.setattr(sources.feedparser, "parse", lambda url: fake)

        cfg = {"categories": ["cs.AI"], "max_per_category": 5}
        items = fetch_arxiv(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc))

        assert len(items) == 2
        assert items[0].source == "arXiv:cs.AI"
        assert items[0].source_type == "arxiv"
        assert items[0].score == 0

    def test_caps_at_max_per_category(self, monkeypatch):
        entries = [
            SimpleNamespace(
                title=f"P{i}",
                link=f"https://arxiv.org/abs/{i}",
                published_parsed=time.struct_time((2026, 5, 20, 0, 0, 0, 0, 0, 0)),
                summary="",
            )
            for i in range(10)
        ]
        fake = SimpleNamespace(entries=entries, bozo=0)
        monkeypatch.setattr(sources.feedparser, "parse", lambda url: fake)
        cfg = {"categories": ["cs.AI"], "max_per_category": 3}
        items = fetch_arxiv(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert len(items) == 3

    def test_failure_returns_empty(self, monkeypatch):
        def boom(url):
            raise RuntimeError("down")

        monkeypatch.setattr(sources.feedparser, "parse", boom)
        cfg = {"categories": ["cs.AI"], "max_per_category": 5}
        assert fetch_arxiv(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc)) == []

    def test_uses_rss_arxiv_org_endpoint(self, monkeypatch):
        """Regression: export.arxiv.org/rss redirects 301 and returns 0 entries."""
        captured: list[str] = []

        def capture(url):
            captured.append(url)
            return SimpleNamespace(entries=[], bozo=0)

        monkeypatch.setattr(sources.feedparser, "parse", capture)
        cfg = {"categories": ["cs.AI", "cs.CL"], "max_per_category": 5}
        fetch_arxiv(cfg, datetime(2020, 1, 1, tzinfo=timezone.utc))
        assert captured == [
            "https://rss.arxiv.org/rss/cs.AI",
            "https://rss.arxiv.org/rss/cs.CL",
        ]


# ============================================================================
# fetch_hf_papers
# ============================================================================


class TestFetchHfPapers:
    @responses.activate
    def test_extracts_papers_above_min_upvotes(self):
        responses.add(
            responses.GET,
            "https://huggingface.co/api/daily_papers",
            json=[
                {
                    "paper": {
                        "id": "2401.12345",
                        "title": "Great Paper",
                        "summary": "Abstract here",
                        "upvotes": 50,
                    },
                    "publishedAt": "2026-05-20T00:00:00.000Z",
                },
                {
                    "paper": {
                        "id": "2401.67890",
                        "title": "Meh Paper",
                        "summary": "Meh abstract",
                        "upvotes": 1,
                    },
                    "publishedAt": "2026-05-20T00:00:00.000Z",
                },
            ],
            status=200,
        )
        cfg = {"days_back": 7, "min_upvotes": 3}
        items = fetch_hf_papers(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert len(items) == 1
        assert items[0].title == "Great Paper"
        assert items[0].score == 50
        assert items[0].url == "https://huggingface.co/papers/2401.12345"
        assert items[0].source == "HF Papers"
        assert items[0].source_type == "hf_papers"

    @responses.activate
    def test_failure_returns_empty(self):
        responses.add(
            responses.GET,
            "https://huggingface.co/api/daily_papers",
            status=500,
        )
        cfg = {"days_back": 7, "min_upvotes": 3}
        assert fetch_hf_papers(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc)) == []


# ============================================================================
# fetch_github
# ============================================================================


class TestFetchGithub:
    @responses.activate
    def test_extracts_releases(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/langchain-ai/langgraph/releases",
            json=[
                {
                    "tag_name": "v0.3.0",
                    "name": "v0.3.0",
                    "html_url": "https://github.com/langchain-ai/langgraph/releases/tag/v0.3.0",
                    "published_at": "2026-05-20T10:00:00Z",
                    "body": "release notes",
                    "prerelease": False,
                    "draft": False,
                }
            ],
            status=200,
        )
        cfg = {"repos": ["langchain-ai/langgraph"], "include_prereleases": False}
        items = fetch_github(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert len(items) == 1
        assert items[0].title == "langchain-ai/langgraph v0.3.0"
        assert items[0].source == "GitHub:langchain-ai/langgraph"
        assert items[0].source_type == "github"
        assert items[0].url.endswith("/v0.3.0")

    @responses.activate
    def test_excludes_old_releases(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/x/y/releases",
            json=[
                {
                    "tag_name": "v0.1",
                    "name": "v0.1",
                    "html_url": "https://github.com/x/y/releases/tag/v0.1",
                    "published_at": "2020-01-01T00:00:00Z",
                    "body": "",
                    "prerelease": False,
                    "draft": False,
                }
            ],
            status=200,
        )
        cfg = {"repos": ["x/y"], "include_prereleases": False}
        items = fetch_github(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert items == []

    @responses.activate
    def test_excludes_prereleases_when_disabled(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/x/y/releases",
            json=[
                {
                    "tag_name": "v0.3.0-rc1",
                    "name": "v0.3.0-rc1",
                    "html_url": "https://github.com/x/y/releases/tag/v0.3.0-rc1",
                    "published_at": "2026-05-20T00:00:00Z",
                    "body": "",
                    "prerelease": True,
                    "draft": False,
                }
            ],
            status=200,
        )
        cfg = {"repos": ["x/y"], "include_prereleases": False}
        items = fetch_github(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert items == []

    @responses.activate
    def test_one_repo_failure_continues(self):
        responses.add(
            responses.GET,
            "https://api.github.com/repos/a/b/releases",
            status=401,
        )
        responses.add(
            responses.GET,
            "https://api.github.com/repos/c/d/releases",
            json=[
                {
                    "tag_name": "v1",
                    "name": "v1",
                    "html_url": "https://github.com/c/d/releases/tag/v1",
                    "published_at": "2026-05-20T00:00:00Z",
                    "body": "",
                    "prerelease": False,
                    "draft": False,
                }
            ],
            status=200,
        )
        cfg = {"repos": ["a/b", "c/d"], "include_prereleases": False}
        items = fetch_github(cfg, datetime(2026, 5, 1, tzinfo=timezone.utc))
        assert len(items) == 1
        assert items[0].source == "GitHub:c/d"
