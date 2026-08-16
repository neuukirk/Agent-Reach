# -*- coding: utf-8 -*-
"""Social-media screening agent built on top of Agent Reach.

This subpackage adds a *negative / adverse-media screening* loop around the
read/search capabilities Agent Reach configures. It is intentionally a thin,
well-seamed layer:

    watchlist  →  sources (Exa / Reddit / Twitter)  →  match  →  dedup  →
    report (Slack)  +  sink (spreadsheet)

Agent Reach itself only installs and routes upstream tools; the screening
agent orchestrates them on a schedule, matches results against a watchlist of
alliance-program entities + risk keywords, deduplicates against prior runs,
and pushes findings to Slack and an optional spreadsheet sink.

Nothing here re-implements platform reading — every source adapter shells out
to the same upstream CLIs Agent Reach configures (mcporter/Exa, OpenCLI/rdt
for Reddit, twitter-cli). Missing or unconfigured backends degrade to a
skipped source with a warning, never a crash.
"""

from agent_reach.screening.models import Finding, RawResult
from agent_reach.screening.screener import run_screening
from agent_reach.screening.state import StateStore
from agent_reach.screening.watchlist import Entity, Watchlist, match_keywords

__all__ = [
    "Finding",
    "RawResult",
    "Watchlist",
    "Entity",
    "match_keywords",
    "StateStore",
    "run_screening",
]
