# -*- coding: utf-8 -*-
"""Watchlist config: alliance-program entities + risk keywords.

The watchlist is a YAML file (see templates/watchlist.example.yaml). It is the
only thing a user must edit to point the screening agent at their program.

This module also owns the *pure* matching logic — given some text and a
watchlist, which risk keywords fired — so it can be unit-tested without any
network or upstream tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

#: Starter negative / adverse-media keyword set. Editable in the watchlist;
#: this list is what `agent-reach screen init` seeds for "help me build both".
DEFAULT_KEYWORDS: List[str] = [
    "lawsuit", "sued", "litigation", "settlement",
    "fraud", "scam", "ponzi", "embezzle",
    "investigation", "probe", "subpoena", "indicted", "charged",
    "sanction", "sanctioned", "fine", "penalty", "violation",
    "bankruptcy", "insolvency", "default", "layoffs", "fired",
    "scandal", "controversy", "misconduct", "allegation", "accused",
    "data breach", "hack", "leak", "outage",
    "recall", "lawsuit filed", "class action",
    "resign", "ousted", "stepped down",
    "boycott", "protest", "complaint",
    "money laundering", "bribery", "corruption", "kickback",
    "discrimination", "harassment", "whistleblower",
]


@dataclass
class Entity:
    """One alliance-program member to screen for."""

    name: str
    aliases: List[str] = field(default_factory=list)

    def terms(self) -> List[str]:
        """All surface forms to look for (name + aliases)."""
        return [self.name, *self.aliases]


@dataclass
class Watchlist:
    """Parsed watchlist config."""

    program: str = "Screening Program"
    entities: List[Entity] = field(default_factory=list)
    keywords: List[str] = field(default_factory=lambda: list(DEFAULT_KEYWORDS))
    sources: Dict[str, bool] = field(
        default_factory=lambda: {"exa": True, "reddit": True, "twitter": True}
    )
    per_query_limit: int = 10
    lookback_days: int = 7

    @classmethod
    def load(cls, path: str | Path) -> "Watchlist":
        """Load and validate a watchlist YAML file."""
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(
                f"Watchlist not found: {p}\n"
                f"Create one with: agent-reach screen init --output {p}"
            )
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Watchlist":
        raw_entities = data.get("entities") or []
        entities: List[Entity] = []
        for item in raw_entities:
            if isinstance(item, str):
                entities.append(Entity(name=item))
            elif isinstance(item, dict) and item.get("name"):
                entities.append(
                    Entity(name=item["name"], aliases=list(item.get("aliases") or []))
                )
        if not entities:
            raise ValueError("Watchlist has no entities — add at least one under 'entities:'")

        keywords = data.get("keywords")
        if not keywords:
            keywords = list(DEFAULT_KEYWORDS)

        sources = {"exa": True, "reddit": True, "twitter": True}
        sources.update({k: bool(v) for k, v in (data.get("sources") or {}).items()})

        options = data.get("options") or {}
        return cls(
            program=data.get("program") or "Screening Program",
            entities=entities,
            keywords=[str(k).strip() for k in keywords if str(k).strip()],
            sources=sources,
            per_query_limit=int(options.get("per_query_limit", 10)),
            lookback_days=int(options.get("lookback_days", 7)),
        )

    def enabled_sources(self) -> List[str]:
        return [s for s, on in self.sources.items() if on]


def _contains_term(text: str, term: str) -> bool:
    """Case-insensitive, word-boundary-aware containment.

    Word boundaries avoid matching 'sued' inside 'pursued' or an entity like
    'Ace' inside 'space'. For multi-word terms we anchor on the whole phrase.
    """
    term = term.strip()
    if not term:
        return False
    pattern = r"(?<!\w)" + re.escape(term) + r"(?!\w)"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def entity_mentioned(text: str, entity: Entity) -> bool:
    """True if any of the entity's terms appears in text."""
    return any(_contains_term(text, term) for term in entity.terms())


def match_keywords(text: str, keywords: List[str]) -> List[str]:
    """Return the subset of keywords present in text (order preserved, deduped)."""
    seen: List[str] = []
    for kw in keywords:
        if _contains_term(text, kw) and kw not in seen:
            seen.append(kw)
    return seen
