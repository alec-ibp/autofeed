from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.dedup import normalize_url
from src.models import Item

log = logging.getLogger(__name__)


def load_seen_urls(path: Path, memory_weeks: int) -> set[str]:
    """Return the union of URLs from the most recent `memory_weeks` entries."""
    if not Path(path).exists():
        return set()
    try:
        data = json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("seen_urls file unreadable (%s); treating as empty", e)
        return set()

    if not isinstance(data, dict):
        return set()

    recent_labels = sorted(data.keys(), reverse=True)[:memory_weeks]
    urls: set[str] = set()
    for label in recent_labels:
        entry = data.get(label) or {}
        for url in entry.get("urls", []) or []:
            urls.add(url)
    return urls


def filter_unseen(items: list[Item], seen_urls: set[str]) -> list[Item]:
    if not seen_urls:
        return list(items)
    return [item for item in items if normalize_url(item.url) not in seen_urls]


def persist_week(path: Path, week_label: str, items: list[Item], memory_weeks: int) -> None:
    """Upsert this week's normalized URLs and prune to last `memory_weeks` entries."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError) as e:
            log.warning("seen_urls file unreadable on persist (%s); rewriting", e)
            data = {}

    data[week_label] = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "urls": [normalize_url(item.url) for item in items],
    }

    kept_labels = sorted(data.keys(), reverse=True)[:memory_weeks]
    data = {label: data[label] for label in kept_labels}

    # Atomic write: tmp + rename
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".seen_urls.", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp_name, path)
    except Exception:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
