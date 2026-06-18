# Screening Agent

Negative / adverse-media screening built on top of Agent Reach. Point it at a
list of alliance-program entities + risk keywords; it searches the web (and
optionally Reddit / Twitter), flags any entity mentioned alongside a risk
keyword, deduplicates against prior runs, and pushes a digest to Slack while
appending to a growing spreadsheet workbook.

## Quick start

```bash
pip install -e .

# 1. scaffold a watchlist, then edit entities + keywords
agent-reach screen init --output watchlist.yaml

# 2. (optional) point Slack at it
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/XXX/YYY/ZZZ"

# 3. run a pass (dry-run prints the report without posting/saving)
agent-reach screen run --watchlist watchlist.yaml --csv findings.csv --dry-run
agent-reach screen run --watchlist watchlist.yaml --csv findings.csv
```

## Pipeline

```
watchlist.yaml  ──or──  members.csv (the 1,400-member base, with firmographics)
   │  entities (+ aliases, domain, industry, location, tier), risk keywords
   ▼
sources         exa (no account) · reddit (login) · twitter (login)
   │  each shells out to the upstream tool Agent Reach configures
   ▼
match           entity mentioned  AND  ≥1 risk keyword     (watchlist.py)
   ▼
disambiguate    confidence = high (domain match) / medium (industry|location)
   │            / low (name only) — the firmographic accuracy layer
   ▼
dedup           seen.json — never re-report the same hit   (state.py)
   │
   ├─► Slack     incoming-webhook digest, grouped by member (notify.py)
   ├─► sink      CSV workbook now; Airtable/Sheets later    (sinks.py)
   └─► --json    structured findings for an n8n AI pipeline (see n8n/)
```

## Screening a member database (the 1,400-member case)

Point the agent at a member-base CSV instead of a hand-written watchlist. Any
export (Google Sheets / Airtable / SQL) works; columns are matched by common
aliases — `name`/`company`, `website`/`domain`, `aliases`, `industry`,
`location`, `tier`, `member_id`/`id`. See `templates/members.example.csv`.

```bash
agent-reach screen run --members members.csv --csv findings.csv --min-confidence medium
agent-reach screen run --members members.csv --json --min-confidence medium   # for n8n
```

Every finding carries `member_id` and a `confidence` tag, so it ties straight
back to the member record and downstream can triage right- vs wrong-entity
hits. `domain` is the highest-value field — it's what lets the agent confirm a
hit is about the *right* same-named company.

## n8n / AI-node orchestration

For the work environment (Slack + Google Sheets + an LLM provider), the
deterministic CLI is one stage in an n8n flow: it pre-filters all 1,400 members
cheaply and emits candidate JSON (`--json`), then n8n AI nodes **verify**
(redundancy check / disambiguation), **triage** (severity + summary), and
deliver. Full design, drop-in prompts, and an importable workflow skeleton are
in [`n8n/README.md`](n8n/README.md).

## Sources & accounts

| Source  | Account needed?                         | Notes |
|---------|-----------------------------------------|-------|
| `exa`   | **No** — safe to run on a schedule      | Full-web/news semantic search via mcporter. Strongest single source for adverse media. |
| `reddit`| Yes — burner account (OpenCLI / rdt-cli)| No anonymous path exists; login mandatory. |
| `twitter`| Yes — burner account (twitter-cli cookie)| Ban risk on programmatic access — never a real account. |

Any source that is missing/unconfigured is **skipped with a warning** — the run
continues with whatever is available. Start with `exa` only; add the social
sources once burner credentials are wired in.

## Scheduling

`.github/workflows/screening.yml` runs weekly (Mondays 13:00 UTC) and commits
the updated CSV + dedup state back to the repo. Set `SLACK_WEBHOOK_URL` (and,
if using Twitter, `TWITTER_AUTH_TOKEN` / `TWITTER_CT0`) as repo secrets.

## Spreadsheet destination

Ships with a CSV workbook sink (`--csv`). Airtable and Google Sheets were left
"decide later" — both connectors are available in the deployment environment,
and a new destination is just a `Sink` subclass in `sinks.py`; the rest of the
pipeline is unchanged.
