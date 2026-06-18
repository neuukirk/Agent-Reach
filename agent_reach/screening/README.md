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
watchlist.yaml
   │  entities (+ aliases), risk keywords, enabled sources
   ▼
sources         exa (no account) · reddit (login) · twitter (login)
   │  each shells out to the upstream tool Agent Reach configures
   ▼
match           entity mentioned  AND  ≥1 risk keyword     (watchlist.py)
   │
   ▼
dedup           seen.json — never re-report the same hit   (state.py)
   │
   ├─► Slack     incoming-webhook digest, grouped by entity (notify.py)
   └─► sink      CSV workbook now; Airtable/Sheets later    (sinks.py)
```

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
