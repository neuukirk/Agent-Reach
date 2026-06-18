# -*- coding: utf-8 -*-
"""CLI glue for the screening agent (`agent-reach screen ...`).

Kept out of cli.py so the main entry point stays lean; cli.py just dispatches
init/run here.
"""

from __future__ import annotations

import datetime as _dt
import importlib.resources
import json
import os
from pathlib import Path
from typing import Optional

from agent_reach.config import Config
from agent_reach.screening.members import load_member_watchlist
from agent_reach.screening.notify import deliver
from agent_reach.screening.screener import run_screening
from agent_reach.screening.sinks import CSVSink, NullSink
from agent_reach.screening.sources import build_sources
from agent_reach.screening.state import StateStore
from agent_reach.screening.watchlist import Watchlist


def _read_template() -> str:
    try:
        pkg = importlib.resources.files("agent_reach.screening").joinpath(
            "templates/watchlist.example.yaml"
        )
        return pkg.read_text(encoding="utf-8")
    except Exception:
        path = Path(__file__).resolve().parent / "templates" / "watchlist.example.yaml"
        return path.read_text(encoding="utf-8")


def cmd_init(output: str) -> int:
    """Write a starter watchlist the user can edit."""
    dest = Path(output).expanduser()
    if dest.exists():
        print(f"[!] {dest} already exists — not overwriting.")
        return 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_read_template(), encoding="utf-8")
    print(f"✅ Wrote starter watchlist to {dest}")
    print("   Edit the entities + keywords, then run:")
    print(f"   agent-reach screen run --watchlist {dest}")
    return 0


def _resolve_webhook(config: Config, explicit: Optional[str]) -> Optional[str]:
    return (
        explicit
        or os.environ.get("SLACK_WEBHOOK_URL")
        or config.get("slack_webhook_url")
    )


def cmd_run(
    watchlist_path: Optional[str] = None,
    members_path: Optional[str] = None,
    csv_path: Optional[str] = None,
    slack_webhook: Optional[str] = None,
    state_path: Optional[str] = None,
    dry_run: bool = False,
    notify_when_empty: bool = False,
    json_output: bool = False,
    min_confidence: str = "low",
) -> int:
    """Execute one screening pass: search → match → disambiguate → dedup → emit.

    Watchlist comes from either a member-base CSV (--members, the 1,400-member
    case) or a hand-written watchlist YAML (--watchlist). JSON output makes the
    findings consumable by an n8n AI verification/severity node.
    """
    config = Config()
    if members_path:
        watchlist = load_member_watchlist(members_path)
    else:
        watchlist = Watchlist.load(watchlist_path or "watchlist.yaml")

    sources = build_sources(watchlist.enabled_sources())
    state = StateStore(state_path)

    findings = run_screening(
        watchlist, sources, state=state, min_confidence=min_confidence
    )

    run_date = _dt.date.today().isoformat()
    webhook = _resolve_webhook(config, slack_webhook)

    if json_output:
        # Machine-readable: the structured stage an n8n flow consumes. No
        # Slack post here — downstream nodes own delivery.
        print(json.dumps([f.to_row() for f in findings], ensure_ascii=False, indent=2))
    else:
        report = deliver(
            watchlist.program,
            findings,
            run_date,
            webhook_url=None if dry_run else webhook,
            notify_when_empty=notify_when_empty,
        )
        print(report)

    sink = CSVSink(csv_path) if csv_path else NullSink()
    if not dry_run:
        sink.write(findings)
        state.save()
    elif not json_output:
        print("\n[dry-run] No Slack post, no sink write, state not saved.")

    return 0
