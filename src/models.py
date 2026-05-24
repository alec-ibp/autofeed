from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime


@dataclass
class Item:
    source: str
    source_type: str
    title: str
    url: str
    published: datetime | None
    score: int
    summary: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat() if self.published else None
        return d


@dataclass
class RankedItem:
    rank: int
    title: str
    url: str
    source: str
    category: str
    why: str

    @classmethod
    def from_dict(cls, d: dict) -> "RankedItem":
        return cls(
            rank=int(d["rank"]),
            title=str(d["title"]),
            url=str(d["url"]),
            source=str(d.get("source", "")),
            category=str(d.get("category", "Otro")),
            why=str(d.get("why", "")),
        )
