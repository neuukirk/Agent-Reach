# -*- coding: utf-8 -*-
"""Dedup state — remember which findings have already been reported.

A flat JSON file of finding ids (see Finding.id). Without this, a weekly run
would re-report every still-live article. State lives next to the watchlist by
default (or under ~/.agent-reach/screening/). Old ids are pruned by count so
the file can't grow without bound across years of weekly runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List

from loguru import logger

_DEFAULT_PATH = Path.home() / ".agent-reach" / "screening" / "seen.json"
_MAX_IDS = 50_000


class StateStore:
    """Persistent set of already-reported finding ids."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else _DEFAULT_PATH
        self._ids: List[str] = []
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._ids = list(data.get("seen", []))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Could not read state {self.path}: {e} — starting fresh")
                self._ids = []

    def seen(self, finding_id: str) -> bool:
        return finding_id in self._ids

    def add(self, finding_ids: Iterable[str]) -> None:
        for fid in finding_ids:
            if fid not in self._ids:
                self._ids.append(fid)

    def save(self) -> None:
        # Keep the most recent ids; we append in chronological order so the
        # tail is freshest.
        if len(self._ids) > _MAX_IDS:
            self._ids = self._ids[-_MAX_IDS:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"seen": self._ids}, ensure_ascii=False), encoding="utf-8"
        )

    def __len__(self) -> int:
        return len(self._ids)
