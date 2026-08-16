# -*- coding: utf-8 -*-
"""Screening orchestrator — tie the pipeline together.

run_screening() is pure-ish and testable: inject the source adapters and a
state store, get back the list of *new* findings. It does not touch Slack or
sinks — the CLI layer composes those around it.
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from agent_reach.screening.models import CONFIDENCE_RANK, Finding
from agent_reach.screening.sources import Source
from agent_reach.screening.state import StateStore
from agent_reach.screening.watchlist import (
    Watchlist,
    disambiguate,
    entity_mentioned,
    match_keywords,
)


def run_screening(
    watchlist: Watchlist,
    sources: List[Source],
    state: Optional[StateStore] = None,
    min_confidence: str = "low",
) -> List[Finding]:
    """Run one screening pass.

    For each enabled source and each entity, search the source for the entity,
    then keep results that (a) genuinely mention the entity, (b) contain at
    least one risk keyword, and (c) clear the min_confidence disambiguation
    bar. New (unseen) findings are returned and recorded in state; the caller
    is responsible for state.save().

    min_confidence: "low" keeps everything (tag only — let a downstream AI node
    verify); "medium"/"high" pre-filter in Python for a tighter, cheaper feed.
    """
    new_findings: List[Finding] = []
    threshold = CONFIDENCE_RANK.get(min_confidence, 1)

    usable: List[Source] = []
    for src in sources:
        ok, reason = src.available()
        if ok:
            logger.info(f"[{src.name}] ready: {reason}")
            usable.append(src)
        else:
            logger.warning(f"[{src.name}] skipped: {reason}")

    if not usable:
        logger.warning("No usable sources — nothing to screen.")
        return []

    for src in usable:
        for entity in watchlist.entities:
            # Query the entity's primary name; alias hits are caught by the
            # entity_mentioned() guard below.
            results = src.search(entity.name, limit=watchlist.per_query_limit)
            for r in results:
                text = r.text()
                if not entity_mentioned(text, entity):
                    continue
                matched = match_keywords(text, watchlist.keywords)
                if not matched:
                    continue
                confidence, reason = disambiguate(text, r.url, entity)
                if CONFIDENCE_RANK[confidence] < threshold:
                    continue
                finding = Finding(
                    entity=entity.name,
                    source=r.source,
                    title=r.title,
                    url=r.url,
                    snippet=r.snippet,
                    matched_keywords=matched,
                    author=r.author,
                    published=r.published,
                    member_id=entity.member_id,
                    domain=entity.domain,
                    confidence=confidence,
                    confidence_reason=reason,
                )
                if state is not None and state.seen(finding.id):
                    continue
                new_findings.append(finding)

    # Dedup within this run (same article for one entity from two sources).
    deduped: List[Finding] = []
    seen_ids = set()
    for f in new_findings:
        if f.id in seen_ids:
            continue
        seen_ids.add(f.id)
        deduped.append(f)

    if state is not None:
        state.add(f.id for f in deduped)

    logger.info(f"Screening produced {len(deduped)} new finding(s)")
    return deduped
