# -*- coding: utf-8 -*-
"""CLI glue for the screening agent (`agent-reach screen ...`).

Kept out of cli.py so the main entry point stays lean; cli.py just dispatches
init/run here.
"""

from __future__ import annotations

import datetime as _dt
import importlib.resources
import os
from pathlib import Path
from typing import Optional

from agent_reach.config import Config
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
    watchlist_path: str,
    csv_path: Optional[str] = None,
    slack_webhook: Optional[str] = None,
    state_path: Optional[str] = None,
    dry_run: bool = False,
    notify_when_empty: bool = False,
) -> int:
    """Execute one screening pass: search → match → dedup → report → log."""
    config = Config()
    watchlist = Watchlist.load(watchlist_path)

    sources = build_sources(watchlist.enabled_sources())
    state = StateStore(state_path)

    findings = run_screening(watchlist, sources, state=state)

    run_date = _dt.date.today().isoformat()
    webhook = _resolve_webhook(config, slack_webhook)

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
    else:
        print("\n[dry-run] No Slack post, no sink write, state not saved.")

    return 0
