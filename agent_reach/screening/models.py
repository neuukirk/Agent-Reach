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


# Confidence tiers, ordered. "high" = firmographic domain match (almost
# certainly the right member); "medium" = a corroborating signal (industry /
# location) present; "low" = name + keyword only (needs the downstream AI
# verification node before trusting). The numeric rank drives --min-confidence.
CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Finding:
    """A matched result: an entity mentioned alongside a risk keyword.

    Carries the member tie-back (member_id, domain) and a disambiguation
    `confidence` so downstream consumers — a spreadsheet, or an n8n AI
    verification node — can triage right vs. wrong-entity hits.
    """

    entity: str
    source: str
    title: str
    url: str
    snippet: str
    matched_keywords: List[str] = field(default_factory=list)
    author: str = ""
    published: Optional[str] = None
    member_id: str = ""
    domain: str = ""
    confidence: str = "low"
    confidence_reason: str = ""

    @property
    def id(self) -> str:
        """Stable dedup id.

        Keyed on (member, source, url) so the same article surfacing for two
        different members is reported once per member, and a re-run never
        re-reports the same hit. Falls back to entity name when no member_id,
        and to the title when a source gives no URL.
        """
        who = self.member_id or self.entity.lower()
        basis = f"{who}|{self.source}|{(self.url or self.title).strip().lower()}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        """Flat dict for spreadsheet/CSV sinks and JSON output (n8n)."""
        return {
            "id": self.id,
            "member_id": self.member_id,
            "entity": self.entity,
            "domain": self.domain,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "matched_keywords": ", ".join(self.matched_keywords),
            "author": self.author,
            "published": self.published or "",
            "snippet": self.snippet,
        }
