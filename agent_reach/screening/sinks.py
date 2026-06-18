# -*- coding: utf-8 -*-
"""Spreadsheet sinks — append findings to a growing workbook.

The destination was left "decide later", so this ships a working CSV sink
(the universal lowest common denominator — opens in Excel/Sheets, easy to
import anywhere) behind a small interface. Airtable and Google Sheets sinks
can be added later as new Sink subclasses without touching the screener.

Both connectors are already available in the deployment environment:
  * Airtable  — see agent_reach/screening/README.md for the planned
                AirtableSink (create_records_for_table).
  * Google Sheets / Drive file store — planned SheetSink.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from loguru import logger

from agent_reach.screening.models import Finding

_COLUMNS = [
    "id", "entity", "source", "title", "url",
    "matched_keywords", "author", "published", "snippet",
]


class Sink:
    """Base sink interface."""

    def write(self, findings: List[Finding]) -> int:
        """Persist findings; return the number written."""
        raise NotImplementedError


class CSVSink(Sink):
    """Append findings to a CSV file (header written once)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    def write(self, findings: List[Finding]) -> int:
        if not findings:
            return 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists()
        with open(self.path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_COLUMNS)
            if new_file:
                writer.writeheader()
            for finding in findings:
                writer.writerow(finding.to_row())
        logger.info(f"Wrote {len(findings)} finding(s) to {self.path}")
        return len(findings)


class NullSink(Sink):
    """No-op sink — used when no spreadsheet destination is configured yet."""

    def write(self, findings: List[Finding]) -> int:
        return 0
