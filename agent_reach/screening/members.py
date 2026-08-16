# -*- coding: utf-8 -*-
"""Ingest a member database (CSV) into a screening Watchlist.

The 1,400-member base is the watchlist. This reads a CSV export — the most
portable format, and what a Google Sheet / Airtable / SQL export all produce —
and maps its columns onto Entity fields, carrying member_id + firmographics
through so every finding ties back to the exact member record.

Column names vary by source, so headers are matched case-insensitively against
a set of common aliases (see _COLUMN_ALIASES). Pass an explicit mapping to
override. In the final n8n deployment, n8n may instead read Google Sheets and
hand rows straight to the screener — load_rows() accepts that path too.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from loguru import logger

from agent_reach.screening.watchlist import DEFAULT_KEYWORDS, Entity, Watchlist

#: Canonical Entity field → accepted header names (lowercased).
_COLUMN_ALIASES: Dict[str, List[str]] = {
    "name": ["name", "company", "company_name", "member", "member_name", "organization", "account"],
    "member_id": ["member_id", "id", "account_id", "crm_id", "record_id"],
    "domain": ["domain", "website", "url", "web", "homepage", "site"],
    "aliases": ["aliases", "alias", "dba", "also_known_as", "aka", "ticker"],
    "industry": ["industry", "sector", "vertical", "naics", "sic"],
    "location": ["location", "hq", "headquarters", "country", "city", "region", "state"],
    "tier": ["tier", "size", "revenue", "segment", "employees", "band"],
}


def _build_header_map(fieldnames: Iterable[str], override: Optional[Dict[str, str]]) -> Dict[str, str]:
    """Map canonical field → actual CSV header present in this file."""
    actual = {fn.strip().lower(): fn for fn in fieldnames if fn}
    mapping: Dict[str, str] = {}
    if override:
        for canon, header in override.items():
            if header and header.strip().lower() in actual:
                mapping[canon] = actual[header.strip().lower()]
    for canon, aliases in _COLUMN_ALIASES.items():
        if canon in mapping:
            continue
        for alias in aliases:
            if alias in actual:
                mapping[canon] = actual[alias]
                break
    return mapping


def _row_to_entity(row: dict, header_map: Dict[str, str]) -> Optional[Entity]:
    def cell(canon: str) -> str:
        header = header_map.get(canon)
        return (row.get(header) or "").strip() if header else ""

    name = cell("name")
    if not name:
        return None

    raw_aliases = cell("aliases")
    aliases = [a.strip() for a in raw_aliases.replace("|", ";").split(";") if a.strip()]
    return Entity(
        name=name,
        aliases=aliases,
        member_id=cell("member_id"),
        domain=cell("domain"),
        industry=cell("industry"),
        location=cell("location"),
        tier=cell("tier"),
    )


def load_rows(
    rows: Iterable[dict],
    fieldnames: Iterable[str],
    *,
    program: str = "Member Screening",
    keywords: Optional[List[str]] = None,
    sources: Optional[Dict[str, bool]] = None,
    column_map: Optional[Dict[str, str]] = None,
    per_query_limit: int = 10,
) -> Watchlist:
    """Build a Watchlist from already-parsed rows (CSV or n8n-supplied)."""
    header_map = _build_header_map(fieldnames, column_map)
    if "name" not in header_map:
        raise ValueError(
            "Could not find a company/name column. Expected one of: "
            f"{_COLUMN_ALIASES['name']}. Pass an explicit column_map to override."
        )

    entities: List[Entity] = []
    skipped = 0
    for row in rows:
        ent = _row_to_entity(row, header_map)
        if ent:
            entities.append(ent)
        else:
            skipped += 1
    if not entities:
        raise ValueError("No usable member rows found (every row missing a name).")

    if skipped:
        logger.warning(f"Skipped {skipped} member row(s) with no name")
    with_domain = sum(1 for e in entities if e.domain)
    logger.info(
        f"Loaded {len(entities)} members ({with_domain} with domains — "
        f"{with_domain * 100 // len(entities)}% high-confidence-capable)"
    )

    return Watchlist(
        program=program,
        entities=entities,
        keywords=list(keywords) if keywords else list(DEFAULT_KEYWORDS),
        sources=sources or {"exa": True, "reddit": True, "twitter": True},
        per_query_limit=per_query_limit,
    )


def load_member_watchlist(
    csv_path: str | Path,
    *,
    program: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    sources: Optional[Dict[str, bool]] = None,
    column_map: Optional[Dict[str, str]] = None,
    per_query_limit: int = 10,
) -> Watchlist:
    """Load a member-base CSV into a Watchlist."""
    path = Path(csv_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"Member CSV not found: {path}")
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    return load_rows(
        rows,
        fieldnames,
        program=program or path.stem.replace("_", " ").title(),
        keywords=keywords,
        sources=sources,
        column_map=column_map,
        per_query_limit=per_query_limit,
    )
