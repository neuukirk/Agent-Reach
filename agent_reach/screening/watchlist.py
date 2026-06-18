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


def normalize_domain(value: str) -> str:
    """Reduce a website/url to a bare registrable-ish domain.

    'https://www.Acme-Corp.com/about' → 'acme-corp.com'. Best-effort: keeps
    everything after the last leading 'www.' and before the first slash.
    """
    if not value:
        return ""
    v = value.strip().lower()
    v = re.sub(r"^[a-z]+://", "", v)        # strip scheme
    v = v.split("/", 1)[0]                   # strip path
    v = re.sub(r"^www\.", "", v)             # strip leading www.
    return v.strip()


@dataclass
class Entity:
    """One alliance-program member to screen for, with firmographics.

    The firmographic fields power disambiguation at scale: `domain` is the
    strongest signal (confirms a hit is the right member), `industry` and
    `location` are corroborators, `tier` drives prioritization.
    """

    name: str
    aliases: List[str] = field(default_factory=list)
    member_id: str = ""
    domain: str = ""
    industry: str = ""
    location: str = ""
    tier: str = ""

    def __post_init__(self) -> None:
        self.domain = normalize_domain(self.domain)

    def terms(self) -> List[str]:
        """All surface forms to look for (name + aliases)."""
        return [self.name, *self.aliases]

    def domain_root(self) -> str:
        """Brand portion of the domain, e.g. 'acme-corp' from 'acme-corp.com'."""
        if not self.domain:
            return ""
        return self.domain.rsplit(".", 1)[0] if "." in self.domain else self.domain

    def corroborators(self) -> List[str]:
        """Firmographic terms that, if present, corroborate a name match."""
        return [t for t in (self.industry, self.location) if t]


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
                    Entity(
                        name=item["name"],
                        aliases=list(item.get("aliases") or []),
                        member_id=str(item.get("member_id") or ""),
                        domain=str(item.get("domain") or item.get("website") or ""),
                        industry=str(item.get("industry") or ""),
                        location=str(item.get("location") or ""),
                        tier=str(item.get("tier") or ""),
                    )
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


def disambiguate(text: str, url: str, entity: Entity) -> tuple[str, str]:
    """Score how confident we are that a name match is the *right* member.

    Returns (confidence, reason):
      high   — the member's domain appears in the result url or text. This is
               near-certain identity, the firmographic payoff at scale.
      medium — a corroborating firmographic term (industry / location) is
               present alongside the name.
      low    — name + keyword only; let a downstream AI verification node make
               the final call before trusting it.
    """
    haystack = f"{url} {text}".lower()
    # High confidence requires the *full* domain (acme.com) in the url or text —
    # the brand root alone would just echo the name match and over-inflate.
    if entity.domain and entity.domain in haystack:
        return "high", f"domain match ({entity.domain})"
    for term in entity.corroborators():
        if _contains_term(text, term):
            return "medium", f"corroborated by '{term}'"
    return "low", "name + keyword only"
