"""Entrypoint / orchestrator for the weekly digest."""
from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from src.dedup import dedup_exact, dedup_semantic, prefilter
from src.email import send_digest
from src.memory import filter_unseen, load_seen_urls, persist_week
from src.ranker import rank_heuristic, rank_llm
from src.render import render_markdown
from src.sources import (
    fetch_arxiv,
    fetch_github,
    fetch_hf_papers,
    fetch_hn,
    fetch_reddit,
    fetch_rss,
)

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(value: Any) -> Any:
    """Recursively replace ${VAR} placeholders in strings with env values.

    Missing env vars expand to "" so the pipeline degrades gracefully (e.g.,
    email skipped if EMAIL_TO unset) instead of crashing on config load.
    """
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def _week_label(now: datetime) -> str:
    iso = now.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _collect_all(config: dict, since: datetime) -> tuple[list, dict[str, int]]:
    """Run all six collectors. Return (items, sources_summary)."""
    rss_items = fetch_rss(config.get("rss", []), since)
    reddit_items = fetch_reddit(config.get("reddit", {}), since)
    hn_items = fetch_hn(config.get("hackernews", {}), since)
    arxiv_items = fetch_arxiv(config.get("arxiv", {}), since)
    hf_items = fetch_hf_papers(config.get("hf_papers", {}), since)
    gh_items = fetch_github(config.get("github", {}), since)

    summary = {
        "rss": len(rss_items),
        "reddit": len(reddit_items),
        "hn": len(hn_items),
        "arxiv": len(arxiv_items),
        "hf_papers": len(hf_items),
        "github": len(gh_items),
    }
    items = rss_items + reddit_items + hn_items + arxiv_items + hf_items + gh_items
    return items, summary


def run_digest(
    config: dict,
    base_dir: Path,
    *,
    now: datetime | None = None,
    use_llm: bool = True,
) -> Path:
    """Run the full pipeline. Returns path to the written digest file."""
    now = now or datetime.now(timezone.utc)
    settings = config.get("settings", {}) or {}

    days_back = int(settings.get("days_back", 7))
    top_n = int(settings.get("top_n", 10))
    max_candidates = int(settings.get("max_candidates", 150))
    memory_weeks = int(settings.get("memory_weeks", 4))
    model = settings.get("model", "")

    since = now - timedelta(days=days_back)
    week_label = _week_label(now)
    base_dir = Path(base_dir)
    digests_dir = base_dir / "digests"
    state_path = digests_dir / "state" / "seen_urls.json"
    digests_dir.mkdir(parents=True, exist_ok=True)

    log.info("Collecting since %s for week %s", since.isoformat(), week_label)
    items, sources_summary = _collect_all(config, since)
    log.info("Collected %d items: %s", len(items), sources_summary)

    items = dedup_exact(items)
    log.info("After dedup_exact: %d", len(items))

    seen = load_seen_urls(state_path, memory_weeks=memory_weeks)
    items = filter_unseen(items, seen)
    log.info("After filter_unseen (memory): %d", len(items))

    if bool(settings.get("semantic_dedup", False)) and items:
        threshold = float(settings.get("semantic_dedup_threshold", 0.85))
        embedding_model = settings.get("embedding_model", "")
        items = dedup_semantic(items, threshold=threshold, embedding_model=embedding_model)
        log.info("After dedup_semantic: %d", len(items))

    items = prefilter(items, max_candidates=max_candidates)
    log.info("After prefilter: %d", len(items))

    ranked: list = []
    if items:
        if use_llm:
            profile = config.get("profile", {}) or {}
            ranked = rank_llm(items, profile, settings)
        else:
            ranked = rank_heuristic(items, top_n=top_n)

    markdown = render_markdown(
        ranked=ranked,
        week_label=week_label,
        generated_at=now,
        model=model,
        sources_summary=sources_summary,
    )

    digest_path = digests_dir / f"{week_label}.md"
    digest_path.write_text(markdown)
    log.info("Wrote digest to %s", digest_path)

    # Persist memory: only the URLs that actually made it into the digest
    persisted_items = [item for item in items if any(r.url == item.url for r in ranked)]
    persist_week(state_path, week_label, persisted_items, memory_weeks=memory_weeks)

    # Optional email delivery
    delivery = config.get("delivery", {}) or {}
    email_cfg = delivery.get("email", {}) or {}
    if email_cfg.get("enabled"):
        sent = send_digest(
            markdown_text=markdown,
            cfg=email_cfg,
            week_label=week_label,
            api_key=os.environ.get("RESEND_API_KEY"),
        )
        log.info("Email send: %s", "ok" if sent else "skipped/failed")

    return digest_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate weekly AI digest")
    parser.add_argument("--config", default="feeds.yaml", help="Path to feeds.yaml")
    parser.add_argument("--base-dir", default=".", help="Project base directory (where digests/ lives)")
    parser.add_argument("--no-llm", action="store_true", help="Force heuristic ranker (skip LLM)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config not found: {config_path}", file=sys.stderr)
        return 1
    config = expand_env(yaml.safe_load(config_path.read_text()))

    digest_path = run_digest(config, base_dir=Path(args.base_dir), use_llm=not args.no_llm)
    print(str(digest_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
