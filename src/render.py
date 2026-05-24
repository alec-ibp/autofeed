from __future__ import annotations

from datetime import datetime

from src.models import RankedItem


def render_markdown(
    ranked: list[RankedItem],
    week_label: str,
    generated_at: datetime,
    model: str,
    sources_summary: dict[str, int],
) -> str:
    lines: list[str] = []
    lines.append(f"# AI Digest — Semana {week_label}")
    lines.append("")
    lines.append(f"_Generado el {generated_at.strftime('%Y-%m-%d %H:%M UTC')} con `{model}`._")
    lines.append("")

    if not ranked:
        lines.append("> Sin items relevantes esta semana.")
        lines.append("")
    else:
        for item in ranked:
            lines.append(f"## {item.rank}. {item.title}")
            lines.append(f"_{item.source} · {item.category}_")
            lines.append("")
            lines.append(item.why)
            lines.append("")
            lines.append(f"[Leer]({item.url})")
            lines.append("")
            lines.append("---")
            lines.append("")

    # Footer
    lines.append("")
    lines.append(f"**{len(ranked)} items.** Fuentes procesadas:")
    for label, count in sorted(sources_summary.items()):
        lines.append(f"- `{label}`: {count}")
    lines.append("")

    return "\n".join(lines)
