# -*- coding: utf-8 -*-
"""Reporting — format findings and push to Slack via incoming webhook.

No Slack MCP/connector is required: the agent posts to a Slack *incoming
webhook* URL you create once (Slack → Apps → Incoming Webhooks) and supply via
the SLACK_WEBHOOK_URL env var or `slack_webhook_url` in Agent Reach config.

When no webhook is configured, format_report() still produces a Markdown
digest the caller can print or write to a file — so the agent is useful before
Slack is wired up.
"""

from __future__ import annotations

from collections import defaultdict
from typing import List, Optional

import requests
from loguru import logger

from agent_reach.screening.models import Finding


def format_report(program: str, findings: List[Finding], run_date: str) -> str:
    """Human-readable Markdown digest grouped by entity."""
    if not findings:
        return f"*{program}* — weekly screening ({run_date})\nNo new flagged mentions. ✅"

    lines = [
        f"*{program}* — weekly screening ({run_date})",
        f"{len(findings)} new flagged mention(s) across "
        f"{len({f.entity for f in findings})} entit(y/ies):",
        "",
    ]
    by_entity = defaultdict(list)
    for f in findings:
        by_entity[f.entity].append(f)

    _conf_icon = {"high": "🟢", "medium": "🟡", "low": "⚪"}
    for entity in sorted(by_entity):
        items = by_entity[entity]
        member = f" `#{items[0].member_id}`" if items[0].member_id else ""
        lines.append(f"*{entity}*{member} ({len(items)})")
        for f in items:
            kw = ", ".join(f.matched_keywords)
            title = f.title or "(untitled)"
            link = f"<{f.url}|{title}>" if f.url else title
            icon = _conf_icon.get(f.confidence, "⚪")
            meta = f"  {icon} _[{f.source}]_ {f.confidence} — keywords: {kw}"
            lines.append(f"• {link}")
            lines.append(meta)
        lines.append("")
    return "\n".join(lines).rstrip()


def post_to_slack(webhook_url: str, text: str, timeout: int = 15) -> bool:
    """Post a message to a Slack incoming webhook. Returns success."""
    if not webhook_url:
        return False
    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=timeout)
        if resp.status_code == 200:
            return True
        logger.warning(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")
        return False
    except requests.RequestException as e:
        logger.warning(f"Slack post failed: {e}")
        return False


def deliver(
    program: str,
    findings: List[Finding],
    run_date: str,
    webhook_url: Optional[str],
    notify_when_empty: bool = False,
) -> str:
    """Format and (optionally) push the report. Returns the report text.

    Skips the Slack post when there are no findings unless notify_when_empty is
    set — most teams don't want a weekly "nothing found" ping, but it's handy
    as a heartbeat that the job ran.
    """
    report = format_report(program, findings, run_date)
    if webhook_url and (findings or notify_when_empty):
        if post_to_slack(webhook_url, report):
            logger.info("Posted screening report to Slack")
    return report
