"""Collectors. Each function returns list[Item] and never raises."""
from __future__ import annotations

import calendar
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests

from src.models import Item

log = logging.getLogger(__name__)

_USER_AGENT = "ai-digest/1.0 (personal use)"
_DEFAULT_TIMEOUT = 15
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")


def _strip_html(text: str | None) -> str:
    if not text:
        return ""
    no_html = _HTML_TAG.sub("", text)
    collapsed = _WHITESPACE.sub(" ", no_html).strip()
    return collapsed[:500]


def _struct_to_dt(parsed) -> datetime | None:
    if not parsed:
        return None
    try:
        ts = calendar.timegm(parsed)
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except Exception:
        return None


def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Tolerate trailing "Z" or millisecond ".000Z"
        v = value.replace("Z", "+00:00")
        return datetime.fromisoformat(v)
    except ValueError:
        return None


# ============================================================================
# RSS
# ============================================================================


def fetch_rss(feeds: list[dict], since: datetime) -> list[Item]:
    items: list[Item] = []
    for feed in feeds:
        name = feed.get("name", "RSS")
        url = feed.get("url")
        if not url:
            continue
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            log.warning("RSS fetch failed for %s: %s", name, e)
            continue

        for entry in getattr(parsed, "entries", []) or []:
            published_struct = getattr(entry, "published_parsed", None) or getattr(
                entry, "updated_parsed", None
            )
            published = _struct_to_dt(published_struct)
            if published is not None and published < since:
                continue
            title = getattr(entry, "title", "") or ""
            link = getattr(entry, "link", "") or ""
            summary = getattr(entry, "summary", None) or getattr(entry, "description", None)
            if not title or not link:
                continue
            items.append(
                Item(
                    source=name,
                    source_type="rss",
                    title=title,
                    url=link,
                    published=published,
                    score=0,
                    summary=_strip_html(summary),
                )
            )
    return items


# ============================================================================
# Reddit
# ============================================================================


def fetch_reddit(cfg: dict, since: datetime) -> list[Item]:
    subs: list[str] = cfg.get("subs", []) or []
    min_score: int = int(cfg.get("min_score", 0))
    limit: int = int(cfg.get("limit_per_sub", 25))

    headers = {"User-Agent": _USER_AGENT}
    items: list[Item] = []

    for sub in subs:
        url = f"https://www.reddit.com/r/{sub}/top.json"
        params = {"t": "week", "limit": str(limit)}
        try:
            r = requests.get(url, params=params, headers=headers, timeout=_DEFAULT_TIMEOUT)
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("Reddit fetch failed for r/%s: %s", sub, e)
            continue

        children = payload.get("data", {}).get("children", []) or []
        for child in children:
            data = child.get("data", {}) or {}
            score = int(data.get("ups", 0) or 0)
            if score < min_score:
                continue
            created = data.get("created_utc")
            published = (
                datetime.fromtimestamp(float(created), tz=timezone.utc)
                if created is not None
                else None
            )
            if published is not None and published < since:
                continue
            is_self = bool(data.get("is_self", False))
            permalink = data.get("permalink", "")
            external_url = data.get("url", "")
            if is_self:
                final_url = (
                    external_url
                    if external_url and "reddit.com" in external_url
                    else f"https://www.reddit.com{permalink}"
                )
            else:
                final_url = external_url or f"https://www.reddit.com{permalink}"
            items.append(
                Item(
                    source=f"r/{sub}",
                    source_type="reddit",
                    title=data.get("title", "") or "",
                    url=final_url,
                    published=published,
                    score=score,
                    summary=_strip_html(data.get("selftext", "")),
                )
            )
    return items


# ============================================================================
# Hacker News (Algolia)
# ============================================================================


def fetch_hn(cfg: dict, since: datetime) -> list[Item]:
    queries: list[str] = cfg.get("queries", []) or []
    min_points: int = int(cfg.get("min_points", 0))
    hits_per_query: int = int(cfg.get("hits_per_query", 20))
    since_ts = int(since.timestamp())

    items: list[Item] = []
    seen_ids: set[str] = set()

    for q in queries:
        params = {
            "query": q,
            "tags": "story",
            "numericFilters": f"created_at_i>{since_ts},points>{min_points}",
            "hitsPerPage": str(hits_per_query),
        }
        try:
            r = requests.get(
                "https://hn.algolia.com/api/v1/search",
                params=params,
                timeout=_DEFAULT_TIMEOUT,
            )
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("HN fetch failed for query %r: %s", q, e)
            continue

        for hit in payload.get("hits", []) or []:
            obj_id = str(hit.get("objectID", ""))
            if not obj_id or obj_id in seen_ids:
                continue
            seen_ids.add(obj_id)
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={obj_id}"
            items.append(
                Item(
                    source="Hacker News",
                    source_type="hn",
                    title=hit.get("title", "") or "",
                    url=url,
                    published=_iso_to_dt(hit.get("created_at")),
                    score=int(hit.get("points", 0) or 0),
                    summary="",
                )
            )
    return items


# ============================================================================
# arXiv
# ============================================================================


def fetch_arxiv(cfg: dict, since: datetime) -> list[Item]:
    categories: list[str] = cfg.get("categories", []) or []
    max_per_category: int = int(cfg.get("max_per_category", 30))

    items: list[Item] = []
    for cat in categories:
        url = f"https://rss.arxiv.org/rss/{cat}"
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            log.warning("arXiv fetch failed for %s: %s", cat, e)
            continue

        count = 0
        for entry in getattr(parsed, "entries", []) or []:
            if count >= max_per_category:
                break
            published_struct = getattr(entry, "published_parsed", None) or getattr(
                entry, "updated_parsed", None
            )
            published = _struct_to_dt(published_struct)
            if published is not None and published < since:
                continue
            items.append(
                Item(
                    source=f"arXiv:{cat}",
                    source_type="arxiv",
                    title=getattr(entry, "title", "") or "",
                    url=getattr(entry, "link", "") or "",
                    published=published,
                    score=0,
                    summary=_strip_html(getattr(entry, "summary", "")),
                )
            )
            count += 1
    return items


# ============================================================================
# Hugging Face Daily Papers
# ============================================================================


def fetch_hf_papers(cfg: dict, since: datetime) -> list[Item]:
    days_back: int = int(cfg.get("days_back", 7))
    min_upvotes: int = int(cfg.get("min_upvotes", 0))

    try:
        r = requests.get(
            "https://huggingface.co/api/daily_papers",
            params={"days": str(days_back)},
            timeout=_DEFAULT_TIMEOUT,
            headers={"User-Agent": _USER_AGENT},
        )
        r.raise_for_status()
        payload = r.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("HF Papers fetch failed: %s", e)
        return []

    if not isinstance(payload, list):
        return []

    items: list[Item] = []
    for entry in payload:
        paper = entry.get("paper", {}) or {}
        upvotes = int(paper.get("upvotes", 0) or 0)
        if upvotes < min_upvotes:
            continue
        published = _iso_to_dt(entry.get("publishedAt"))
        if published is not None and published < since:
            continue
        paper_id = paper.get("id", "")
        if not paper_id:
            continue
        items.append(
            Item(
                source="HF Papers",
                source_type="hf_papers",
                title=paper.get("title", "") or "",
                url=f"https://huggingface.co/papers/{paper_id}",
                published=published,
                score=upvotes,
                summary=_strip_html(paper.get("summary", "")),
            )
        )
    return items


# ============================================================================
# GitHub Releases
# ============================================================================


def fetch_github(cfg: dict, since: datetime) -> list[Item]:
    repos: list[str] = cfg.get("repos", []) or []
    include_prereleases: bool = bool(cfg.get("include_prereleases", False))

    headers = {"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    items: list[Item] = []
    for repo in repos:
        url = f"https://api.github.com/repos/{repo}/releases"
        try:
            r = requests.get(
                url,
                params={"per_page": "10"},
                headers=headers,
                timeout=_DEFAULT_TIMEOUT,
            )
            r.raise_for_status()
            releases = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("GitHub fetch failed for %s: %s", repo, e)
            continue

        if not isinstance(releases, list):
            continue

        for rel in releases:
            if rel.get("draft", False):
                continue
            if rel.get("prerelease", False) and not include_prereleases:
                continue
            published = _iso_to_dt(rel.get("published_at"))
            if published is not None and published < since:
                continue
            tag = rel.get("tag_name", "")
            items.append(
                Item(
                    source=f"GitHub:{repo}",
                    source_type="github",
                    title=f"{repo} {tag}".strip(),
                    url=rel.get("html_url", "") or "",
                    published=published,
                    score=0,
                    summary=_strip_html(rel.get("body", "")),
                )
            )
    return items
