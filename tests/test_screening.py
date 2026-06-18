# -*- coding: utf-8 -*-
"""Tests for the screening agent."""


import pytest

from agent_reach.screening.models import Finding, RawResult
from agent_reach.screening.notify import format_report
from agent_reach.screening.screener import run_screening
from agent_reach.screening.sinks import CSVSink
from agent_reach.screening.sources import Source, _coerce_results, _parse_loose_json
from agent_reach.screening.state import StateStore
from agent_reach.screening.watchlist import (
    DEFAULT_KEYWORDS,
    Entity,
    Watchlist,
    entity_mentioned,
    match_keywords,
)

# ── matching ────────────────────────────────────────────────

class TestMatching:
    def test_keyword_match_word_boundary(self):
        # "sued" must not match inside "pursued"
        assert match_keywords("they pursued a deal", ["sued"]) == []
        assert match_keywords("the company was sued today", ["sued"]) == ["sued"]

    def test_keyword_match_case_insensitive(self):
        assert match_keywords("Major FRAUD probe", ["fraud"]) == ["fraud"]

    def test_multiword_keyword(self):
        assert match_keywords("a class action was filed", ["class action"]) == ["class action"]
        assert match_keywords("classroom action figures", ["class action"]) == []

    def test_match_preserves_order_and_dedup(self):
        text = "fraud and lawsuit and fraud again"
        assert match_keywords(text, ["lawsuit", "fraud"]) == ["lawsuit", "fraud"]

    def test_entity_mentioned_alias(self):
        e = Entity(name="Acme Corporation", aliases=["ACME"])
        assert entity_mentioned("ACME hit with fine", e)
        assert entity_mentioned("Acme Corporation news", e)
        assert not entity_mentioned("acmestic products", e)


# ── watchlist loading ───────────────────────────────────────

class TestWatchlist:
    def test_from_dict_minimal(self):
        wl = Watchlist.from_dict({"entities": ["Globex"]})
        assert wl.entities[0].name == "Globex"
        assert wl.keywords == DEFAULT_KEYWORDS

    def test_from_dict_full(self):
        wl = Watchlist.from_dict({
            "program": "P",
            "entities": [{"name": "Acme", "aliases": ["A"]}],
            "keywords": ["fraud"],
            "sources": {"exa": True, "reddit": False, "twitter": False},
            "options": {"per_query_limit": 5, "lookback_days": 14},
        })
        assert wl.program == "P"
        assert wl.entities[0].aliases == ["A"]
        assert wl.keywords == ["fraud"]
        assert wl.enabled_sources() == ["exa"]
        assert wl.per_query_limit == 5

    def test_no_entities_raises(self):
        with pytest.raises(ValueError):
            Watchlist.from_dict({"entities": []})

    def test_load_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Watchlist.load("/nonexistent/watchlist.yaml")


# ── source parsing ──────────────────────────────────────────

class TestSourceParsing:
    def test_coerce_list(self):
        payload = [{"title": "T", "url": "http://x", "snippet": "S"}]
        rows = _coerce_results(payload, "exa")
        assert rows[0].title == "T" and rows[0].url == "http://x"

    def test_coerce_wrapped_dict(self):
        payload = {"results": [{"title": "T"}]}
        rows = _coerce_results(payload, "reddit")
        assert len(rows) == 1 and rows[0].source == "reddit"

    def test_parse_jsonl(self):
        out = '{"title":"A"}\n{"title":"B"}'
        rows = _parse_loose_json(out, "twitter")
        assert [r.title for r in rows] == ["A", "B"]

    def test_parse_empty(self):
        assert _parse_loose_json("", "exa") == []
        assert _parse_loose_json("not json", "exa") == []


# ── orchestration ───────────────────────────────────────────

class FakeSource(Source):
    def __init__(self, name, results):
        self.name = name
        self._results = results

    def available(self):
        return True, "fake"

    def search(self, query, limit=10):
        return list(self._results)


class TestScreener:
    def _watchlist(self):
        return Watchlist.from_dict({
            "entities": [{"name": "Acme"}],
            "keywords": ["fraud", "lawsuit"],
            "sources": {"exa": True},
        })

    def test_flags_entity_plus_keyword(self):
        src = FakeSource("exa", [
            RawResult(source="exa", title="Acme hit by fraud probe", url="http://a"),
            RawResult(source="exa", title="Acme launches product", url="http://b"),
            RawResult(source="exa", title="Globex fraud", url="http://c"),
        ])
        findings = run_screening(self._watchlist(), [src], state=None)
        assert len(findings) == 1
        assert findings[0].matched_keywords == ["fraud"]
        assert findings[0].url == "http://a"

    def test_dedup_within_run(self):
        dup = RawResult(source="exa", title="Acme fraud", url="http://a")
        src = FakeSource("exa", [dup, dup])
        findings = run_screening(self._watchlist(), [src], state=None)
        assert len(findings) == 1

    def test_state_suppresses_seen(self, tmp_path):
        state = StateStore(tmp_path / "seen.json")
        src = FakeSource("exa", [RawResult(source="exa", title="Acme fraud", url="http://a")])
        first = run_screening(self._watchlist(), [src], state=state)
        assert len(first) == 1
        state.save()
        # Second run with same state: already seen → no new findings
        state2 = StateStore(tmp_path / "seen.json")
        second = run_screening(self._watchlist(), [src], state=state2)
        assert second == []

    def test_unavailable_source_skipped(self):
        class Dead(Source):
            name = "dead"
            def available(self):
                return False, "nope"
            def search(self, query, limit=10):
                raise AssertionError("should not be called")

        findings = run_screening(self._watchlist(), [Dead()], state=None)
        assert findings == []


# ── state ───────────────────────────────────────────────────

class TestState:
    def test_roundtrip(self, tmp_path):
        s = StateStore(tmp_path / "s.json")
        s.add(["a", "b"])
        assert s.seen("a") and not s.seen("z")
        s.save()
        s2 = StateStore(tmp_path / "s.json")
        assert s2.seen("b") and len(s2) == 2

    def test_corrupt_file_starts_fresh(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("{not json")
        s = StateStore(p)
        assert len(s) == 0


# ── reporting + sink ────────────────────────────────────────

class TestReporting:
    def test_empty_report(self):
        r = format_report("Prog", [], "2026-06-18")
        assert "No new flagged mentions" in r

    def test_report_groups_by_entity(self):
        findings = [
            Finding(entity="Acme", source="exa", title="t1", url="http://a",
                    snippet="", matched_keywords=["fraud"]),
            Finding(entity="Globex", source="reddit", title="t2", url="http://b",
                    snippet="", matched_keywords=["lawsuit"]),
        ]
        r = format_report("Prog", findings, "2026-06-18")
        assert "Acme" in r and "Globex" in r and "fraud" in r

    def test_csv_sink_appends(self, tmp_path):
        path = tmp_path / "out.csv"
        sink = CSVSink(path)
        f = Finding(entity="Acme", source="exa", title="t", url="http://a",
                    snippet="s", matched_keywords=["fraud"])
        assert sink.write([f]) == 1
        sink.write([f])
        content = path.read_text()
        # header once, two data rows
        assert content.count("Acme") == 2
        assert content.count("entity,source") == 1 or content.splitlines()[0].startswith("id,")


# ── finding identity ────────────────────────────────────────

class TestFinding:
    def test_id_stable_and_entity_scoped(self):
        f1 = Finding(entity="Acme", source="exa", title="t", url="http://a", snippet="")
        f2 = Finding(entity="Acme", source="exa", title="different", url="http://a", snippet="")
        f3 = Finding(entity="Globex", source="exa", title="t", url="http://a", snippet="")
        assert f1.id == f2.id      # same entity+source+url
        assert f1.id != f3.id      # different entity

    def test_to_row_keys(self):
        f = Finding(entity="Acme", source="exa", title="t", url="u", snippet="s",
                    matched_keywords=["fraud", "fine"])
        row = f.to_row()
        assert row["matched_keywords"] == "fraud, fine"
        assert row["entity"] == "Acme"
