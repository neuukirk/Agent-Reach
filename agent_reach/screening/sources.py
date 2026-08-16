# -*- coding: utf-8 -*-
"""Source adapters — shell out to the upstream tools Agent Reach configures.

Each adapter wraps exactly one of the sources the user picked (Exa web/news,
Reddit, Twitter/X). It exposes:

    available() -> (bool, str)   # is the backend installed/usable? + reason
    search(query, limit) -> List[RawResult]

Design rules (mirroring Agent Reach's "glue layer, no wrapper" philosophy):
  * We never re-implement reading — we invoke the same CLIs (mcporter/Exa,
    OpenCLI or rdt-cli for Reddit, twitter-cli) and parse their stdout.
  * A missing/unconfigured/broken backend returns available()=False and
    search() returns [] — the run continues with the remaining sources.
  * Output shapes differ per tool and drift over time, so parsing is
    defensive and heuristic: we try JSON first, map common field names, and
    fall back to empty rather than raising.

Exact tool/flag signatures can be confirmed at runtime with:
    mcporter tools exa      |  opencli reddit search --help  |  twitter --help
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import List, Optional, Tuple

from loguru import logger

from agent_reach.screening.models import RawResult


def _run(cmd: List[str], timeout: int = 45) -> Tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr). rc=-1 on exec failure."""
    try:
        r = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout
        )
        return r.returncode, r.stdout or "", r.stderr or ""
    except (FileNotFoundError, OSError) as e:
        return -1, "", str(e)
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"


def _coerce_results(payload, source: str) -> List[RawResult]:
    """Best-effort: turn an arbitrary JSON payload into RawResults.

    Handles the common shapes upstream tools emit: a top-level list, or a dict
    wrapping the list under 'results'/'data'/'items'/'posts'/'tweets'.
    """
    if isinstance(payload, dict):
        for key in ("results", "data", "items", "posts", "tweets", "hits"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        return []

    out: List[RawResult] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        out.append(
            RawResult(
                source=source,
                title=str(item.get("title") or item.get("text") or item.get("body") or "")[:500],
                url=str(item.get("url") or item.get("link") or item.get("permalink") or ""),
                snippet=str(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("text")
                    or item.get("selftext")
                    or item.get("body")
                    or ""
                )[:1000],
                author=str(
                    item.get("author") or item.get("user") or item.get("username") or ""
                ),
                published=(
                    item.get("published")
                    or item.get("publishedDate")
                    or item.get("created_utc")
                    or item.get("date")
                ),
            )
        )
    return out


class Source:
    """Base source adapter."""

    name: str = ""

    def available(self) -> Tuple[bool, str]:
        raise NotImplementedError

    def search(self, query: str, limit: int = 10) -> List[RawResult]:
        raise NotImplementedError


class ExaSource(Source):
    """Full-web / news semantic search via Exa, called through mcporter.

    No account needed — safe to run on a schedule. This is the strongest
    single source for adverse-media screening.
    """

    name = "exa"

    def available(self) -> Tuple[bool, str]:
        if not shutil.which("mcporter"):
            return False, "mcporter not installed (npm install -g mcporter)"
        rc, out, _ = _run(["mcporter", "config", "list"], timeout=10)
        if rc != 0:
            return False, "mcporter not responding"
        if "exa" not in out.lower():
            return False, "Exa not configured (mcporter config add exa https://mcp.exa.ai/mcp)"
        return True, "Exa via mcporter"

    def search(self, query: str, limit: int = 10) -> List[RawResult]:
        # mcporter invokes MCP tools as `tool.method(arg=value)` — same call
        # shape Agent Reach uses elsewhere (see cli.py xiaohongshu probe).
        call = f'exa.web_search_exa(query="{query}", numResults={int(limit)})'
        rc, out, err = _run(["mcporter", "call", call], timeout=60)
        if rc != 0:
            logger.warning(f"[exa] search failed: {err.strip()[:200]}")
            return []
        return _parse_loose_json(out, self.name)


class RedditSource(Source):
    """Reddit search via OpenCLI (preferred) or rdt-cli. Login required."""

    name = "reddit"

    def __init__(self) -> None:
        self._backend: Optional[str] = None

    def available(self) -> Tuple[bool, str]:
        if shutil.which("opencli"):
            self._backend = "opencli"
            return True, "OpenCLI (browser session)"
        if shutil.which("rdt"):
            self._backend = "rdt"
            return True, "rdt-cli"
        return False, "no Reddit backend (install OpenCLI or rdt-cli; login required)"

    def search(self, query: str, limit: int = 10) -> List[RawResult]:
        if self._backend is None:
            ok, _ = self.available()
            if not ok:
                return []
        if self._backend == "opencli":
            cmd = ["opencli", "reddit", "search", query, "-f", "json"]
        else:
            cmd = ["rdt", "search", query, "--json", "--limit", str(int(limit))]
        rc, out, err = _run(cmd, timeout=60)
        if rc != 0:
            logger.warning(f"[reddit] search failed: {err.strip()[:200]}")
            return []
        return _parse_loose_json(out, self.name)[:limit]


class TwitterSource(Source):
    """Twitter/X search via twitter-cli (cookie auth). Login required."""

    name = "twitter"

    def available(self) -> Tuple[bool, str]:
        if not shutil.which("twitter"):
            return False, "twitter-cli not installed (pipx install twitter-cli)"
        rc, out, _ = _run(["twitter", "status"], timeout=15)
        if rc != 0 or "ok: true" not in (out.lower()):
            return False, "twitter-cli not authenticated (configure cookies)"
        return True, "twitter-cli"

    def search(self, query: str, limit: int = 10) -> List[RawResult]:
        rc, out, err = _run(
            ["twitter", "search", query, "--json", "--limit", str(int(limit))], timeout=60
        )
        if rc != 0:
            logger.warning(f"[twitter] search failed: {err.strip()[:200]}")
            return []
        return _parse_loose_json(out, self.name)[:limit]


def _parse_loose_json(out: str, source: str) -> List[RawResult]:
    """Parse stdout that is *mostly* JSON.

    Upstream CLIs sometimes prefix a banner line or emit JSONL. Try a strict
    parse first, then line-by-line JSONL, then bail to empty.
    """
    out = out.strip()
    if not out:
        return []
    try:
        return _coerce_results(json.loads(out), source)
    except json.JSONDecodeError:
        pass
    rows: List[RawResult] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line[0] not in "{[":
            continue
        try:
            rows.extend(_coerce_results(json.loads(line), source))
        except json.JSONDecodeError:
            continue
    return rows


#: Registry: source name → adapter factory.
SOURCE_REGISTRY = {
    "exa": ExaSource,
    "reddit": RedditSource,
    "twitter": TwitterSource,
}


def build_sources(names: List[str]) -> List[Source]:
    """Instantiate adapters for the named sources (unknown names skipped)."""
    out: List[Source] = []
    for n in names:
        factory = SOURCE_REGISTRY.get(n)
        if factory:
            out.append(factory())
        else:
            logger.warning(f"Unknown source '{n}' — skipping")
    return out
