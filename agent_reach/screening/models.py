# -*- coding: utf-8 -*-
"""Data models for the screening agent.

Two tiny dataclasses move through the pipeline:

  RawResult  — a single search hit as returned by a source adapter, before
               any keyword matching. Field mapping is best-effort across
               heterogeneous upstream tools.
  Finding    — a RawResult that matched at least one watched entity AND at
               least one risk keyword. This is what gets reported and logged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RawResult:
    """One search hit from a source adapter, pre-matching."""

    source: str                      # "exa" | "reddit" | "twitter"
    title: str = ""
    url: str = ""
    snippet: str = ""
    author: str = ""
    published: Optional[str] = None  # ISO-ish date string when available

    def text(self) -> str:
        """Concatenated searchable text (title + snippet + author)."""
        return " ".join(p for p in (self.title, self.snippet, self.author) if p)


@dataclass
class Finding:
    """A matched result: an entity mentioned alongside a risk keyword."""

    entity: str
    source: str
    title: str
    url: str
    snippet: str
    matched_keywords: List[str] = field(default_factory=list)
    author: str = ""
    published: Optional[str] = None

    @property
    def id(self) -> str:
        """Stable dedup id.

        Keyed on (entity, source, url) so the same article surfacing for two
        different alliance entities is reported once per entity, and a re-run
        never re-reports the same hit. Falls back to the title when a source
        gives no URL.
        """
        basis = f"{self.entity.lower()}|{self.source}|{(self.url or self.title).strip().lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        """Flat dict for spreadsheet/CSV sinks."""
        return {
            "id": self.id,
            "entity": self.entity,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "matched_keywords": ", ".join(self.matched_keywords),
            "author": self.author,
            "published": self.published or "",
            "snippet": self.snippet,
        }
