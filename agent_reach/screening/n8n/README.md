# n8n integration — member screening with AI verification

This is the orchestration design for running the screening agent inside n8n in
your work environment, where Slack, Google Sheets, and an LLM provider are all
connected. It implements the "specialized agents / redundancy checks" pattern
you described.

## Core principle

**Cheap deterministic work over all 1,400 members; expensive AI only over the
candidates that survive.** The Python CLI does entity-match + risk-keyword +
firmographic disambiguation across the whole member base and emits a small
candidate list as JSON. n8n's AI nodes then verify, triage, and summarize that
short list. This keeps cost and latency low and accuracy high.

```
[Schedule Trigger]  weekly
      │
[Google Sheets: read members]  ── or skip; the CLI can read a committed CSV
      │
[Execute Command]  agent-reach screen run --members members.csv --json --min-confidence medium
      │            → JSON array of candidate findings (member_id, domain, confidence, keywords, url, snippet)
[Code: parse stdout → items]
      │
[Split In Batches]  one finding at a time
      │
[AI Agent #1 — VERIFY]  redundancy check: "is this really about THIS member?"  → {verified, why}
      │
[Filter]  keep verified == true
      │
[AI Agent #2 — TRIAGE]  severity (critical/material/minor) + one-line summary
      │
[Code: semantic de-dup]  collapse the same story across sources (optional)
      │
      ├── [Slack]  post critical/material to the channel
      └── [Google Sheets: append]  log everything to the workbook
```

## Why each AI node exists

| Node | Job | Why an LLM (not code) |
|------|-----|------------------------|
| **Verify** (redundancy) | Given the member's firmographics + the article, decide if it's genuinely about this member. | Disambiguation is judgement: "Acme the Ohio manufacturer" vs "Acme the London startup". This is your false-positive guard. |
| **Triage** | Severity + a human summary. | "Material adverse" vs "minor mention" is nuanced; summarizing is language work. |
| **Consensus** (optional) | Require two passes/models to agree before a *critical* Slack ping. | Extra redundancy for the highest-stakes alerts. |

The Python layer already attaches a `confidence` tag (high/medium/low) from the
firmographic domain match — the Verify node is the AI second opinion on top of
it, especially for the `medium`/`low` ones.

## AI node prompts (drop-in)

**Verify agent — system prompt:**
```
You are a screening analyst. Decide whether a news/social result is about a
SPECIFIC member company, not a same-named different entity.

Member profile:
  name: {{ $json.entity }}
  domain: {{ $json.domain }}
  industry: {{ $json.industry }}
  location: {{ $json.location }}

Result:
  title: {{ $json.title }}
  url: {{ $json.url }}
  snippet: {{ $json.snippet }}

Reply as JSON: {"verified": true|false, "why": "<one sentence>"}.
Mark verified=false if the result is about a different company that merely
shares the name, or if there is no real adverse-news content.
```

**Triage agent — system prompt:**
```
Classify the adverse-news severity for this confirmed member hit and write a
one-sentence summary an account manager can read at a glance.

Matched risk terms: {{ $json.matched_keywords }}
Title: {{ $json.title }}
Snippet: {{ $json.snippet }}

Reply as JSON:
{"severity": "critical|material|minor", "summary": "<one sentence>"}
critical = lawsuit/fraud/insolvency/criminal; material = investigation/fine/
layoffs/leadership exit; minor = soft controversy or passing mention.
```

## The Execute Command stage

The CLI is the deterministic pre-filter. Run it however your n8n host invokes
shell commands (Execute Command node) or wrap it behind a tiny HTTP endpoint:

```bash
agent-reach screen run \
  --members /data/members.csv \
  --json \
  --min-confidence medium
```

`--json` prints a findings array to stdout (no Slack post — n8n owns delivery).
`--min-confidence medium` drops name-only guesses so the AI nodes only see
plausible hits; set `low` if you'd rather let the Verify agent see everything.

Each JSON item already carries `member_id`, so the final Google Sheets append
ties every row straight back to your member record.

## Porting checklist (work environment)

- [ ] Member base in Google Sheets (or export to `members.csv`).
- [ ] Slack node → your screening channel.
- [ ] LLM provider credential for the AI Agent nodes.
- [ ] Google Sheets node → the findings workbook (append mode).
- [ ] Schedule Trigger → weekly.
- [ ] Persist the dedup state (`--state`) on a volume, or use a Sheets/DB
      lookup node to dedupe by `id` instead.

`screening.workflow.json` in this folder is an importable skeleton with the
Schedule → Execute Command → parse → Slack backbone and placeholder AI nodes to
fill in with your provider.
