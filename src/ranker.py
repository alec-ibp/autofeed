from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import litellm

from src.models import Item, RankedItem

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_ROUGH_BATCH_SIZE = 30


# ============================================================================
# Heuristic fallback
# ============================================================================


def _recency_key(item: Item) -> tuple[int, datetime]:
    if item.published is None:
        return (0, datetime.min.replace(tzinfo=timezone.utc))
    return (1, item.published)


def rank_heuristic(items: list[Item], top_n: int) -> list[RankedItem]:
    ordered = sorted(items, key=lambda i: (i.score, _recency_key(i)), reverse=True)
    selected = ordered[:top_n]
    return [
        RankedItem(
            rank=idx + 1,
            title=item.title,
            url=item.url,
            source=item.source,
            category="Otro",
            why="",
        )
        for idx, item in enumerate(selected)
    ]


# ============================================================================
# Prompt construction
# ============================================================================


def _render_profile(profile: dict) -> str:
    lines: list[str] = []
    lines.append("PRIORITIES (in order):")
    for p in profile.get("priorities", []) or []:
        lines.append(f"- {p}")
    lines.append("")
    lines.append("ANTI-PRIORITIES (avoid):")
    for ap in profile.get("anti_priorities", []) or []:
        lines.append(f"- {ap}")
    excl = profile.get("exclude_keywords", []) or []
    if excl:
        lines.append("")
        lines.append("EXCLUDE if title/summary contains (case-insensitive): " + ", ".join(excl))
    notes = profile.get("extra_notes", "") or ""
    if notes.strip():
        lines.append("")
        lines.append("EXTRA NOTES: " + notes.strip())
    return "\n".join(lines)


def _items_to_payload(items: list[Item]) -> list[dict]:
    return [
        {
            "idx": i,
            "title": item.title,
            "source": item.source,
            "url": item.url,
            "published": item.published.isoformat() if item.published else None,
            "score": item.score,
            "summary": item.summary,
        }
        for i, item in enumerate(items)
    ]


def _build_single_pass_messages(items: list[Item], profile: dict, top_n: int) -> list[dict]:
    profile_block = _render_profile(profile)
    payload = json.dumps(_items_to_payload(items), ensure_ascii=False)
    system = (
        "You curate a weekly AI digest. Respond ONLY with valid JSON. "
        "No markdown fences, no preamble, no commentary."
    )
    user = (
        f"Pick the Top {top_n} most useful items for this profile.\n\n"
        f"{profile_block}\n\n"
        f"For each pick write `why` in 1-2 concrete sentences explaining the value for this profile. "
        f"`url` MUST be one of the URLs in the candidates below — do not invent. "
        f"`category` must be one of: Agentes | Frameworks | Metodología | Releases | Paper | Otro.\n\n"
        f"Output schema:\n"
        f"{{\"items\": [{{\"rank\": int, \"title\": str, \"url\": str, "
        f"\"source\": str, \"category\": str, \"why\": str}}]}}\n\n"
        f"Candidates (JSON array):\n{payload}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ============================================================================
# JSON parsing / validation
# ============================================================================


def _strip_fences(text: str) -> str:
    s = text.strip()
    if s.startswith("```"):
        # Remove opening fence and optional language tag
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        if s.endswith("```"):
            s = s[: -len("```")]
        s = s.strip()
    return s


def _parse_ranked(content: str, candidates: list[Item], top_n: int) -> list[RankedItem]:
    raw = _strip_fences(content)
    data = json.loads(raw)  # may raise
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("response missing 'items' list")

    candidate_urls = {item.url for item in candidates}
    result: list[RankedItem] = []
    for entry in data["items"]:
        if not isinstance(entry, dict):
            continue
        url = str(entry.get("url", ""))
        if url not in candidate_urls:
            log.warning("Discarding item with non-candidate URL: %s", url)
            continue
        result.append(RankedItem.from_dict(entry))

    # Re-number ranks 1..N in case the model gave inconsistent ranks
    result = result[:top_n]
    for i, r in enumerate(result):
        r.rank = i + 1
    return result


# ============================================================================
# Single-pass ranking
# ============================================================================


def _call_completion(messages: list[dict], model: str, temperature: float) -> str:
    response = litellm.completion(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    return response.choices[0].message.content


def _rank_single_pass(items: list[Item], profile: dict, settings: dict) -> list[RankedItem]:
    top_n = int(settings.get("top_n", 10))
    model = settings.get("model", "")
    temperature = float(settings.get("temperature", 0.3))
    messages = _build_single_pass_messages(items, profile, top_n)

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            content = _call_completion(messages, model, temperature)
            return _parse_ranked(content, items, top_n)
        except Exception as e:
            last_error = e
            log.warning("LLM ranking attempt %d failed: %s", attempt, e)

    log.error("LLM ranking failed after retries (%s); falling back to heuristic", last_error)
    return rank_heuristic(items, top_n)


def _build_rough_messages(batch: list[Item], profile: dict) -> list[dict]:
    profile_block = _render_profile(profile)
    payload = json.dumps(
        [
            {"idx": i, "title": item.title, "source": item.source, "summary": item.summary}
            for i, item in enumerate(batch)
        ],
        ensure_ascii=False,
    )
    system = (
        "You triage AI/research candidates against a profile. "
        "Respond ONLY with valid JSON. No markdown fences, no commentary."
    )
    user = (
        f"For each candidate, output a label: 'keep' if clearly aligned with PRIORITIES, "
        f"'discard' if clearly aligned with ANTI-PRIORITIES or low signal, "
        f"'maybe' otherwise. Be selective with 'discard'.\n\n"
        f"{profile_block}\n\n"
        f"Output schema: {{\"results\": [{{\"idx\": int, \"label\": \"keep|maybe|discard\"}}]}}\n\n"
        f"Candidates (idx is position in this batch):\n{payload}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _classify_batch(batch: list[Item], profile: dict, model: str, temperature: float) -> list[str]:
    """Return list of labels (keep/maybe/discard) aligned with batch indices.

    On failure, returns all 'maybe' for graceful degradation.
    """
    messages = _build_rough_messages(batch, profile)
    try:
        content = _call_completion(messages, model, temperature)
        raw = _strip_fences(content)
        data = json.loads(raw)
        results = data.get("results", []) if isinstance(data, dict) else []
    except Exception as e:
        log.warning("Rough batch classification failed (%s); passing all as 'maybe'", e)
        return ["maybe"] * len(batch)

    labels = ["maybe"] * len(batch)
    for entry in results:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("idx"))
            label = str(entry.get("label", "maybe")).lower()
        except (TypeError, ValueError):
            continue
        if 0 <= idx < len(batch) and label in {"keep", "maybe", "discard"}:
            labels[idx] = label
    return labels


def _rank_two_pass(items: list[Item], profile: dict, settings: dict) -> list[RankedItem]:
    model = settings.get("model", "")
    temperature = float(settings.get("temperature", 0.3))

    survivors: list[Item] = []
    for start in range(0, len(items), _ROUGH_BATCH_SIZE):
        batch = items[start : start + _ROUGH_BATCH_SIZE]
        labels = _classify_batch(batch, profile, model, temperature)
        for item, label in zip(batch, labels):
            if label != "discard":
                survivors.append(item)

    if not survivors:
        log.warning("Rough pass discarded everything; falling back to all items for fine pass")
        survivors = items

    return _rank_single_pass(survivors, profile, settings)


def rank_llm(items: list[Item], profile: dict, settings: dict) -> list[RankedItem]:
    """Rank items via LLM. Strategy: single | two-pass | auto."""
    if not items:
        return []
    strategy = settings.get("ranking_strategy", "auto")
    threshold = int(settings.get("two_pass_threshold", 50))

    use_two_pass = strategy == "two-pass" or (strategy == "auto" and len(items) > threshold)
    if use_two_pass:
        return _rank_two_pass(items, profile, settings)
    return _rank_single_pass(items, profile, settings)
