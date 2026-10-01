# kindle-niche-snapshot

Weekly snapshot of Kindle nonfiction niche data, consumed by the `clawcreek-howto-author` skill.
The skill runs in a sandbox and downloads these files via their `raw.githubusercontent.com` URLs,
e.g. `https://raw.githubusercontent.com/clawcreek/kindle-niche-snapshot/main/snapshot/manifest.json`.

## Update frequency

Once a week. The snapshot is meant to be produced by `scripts/refresh.sh`, run weekly on the maintainer's
own machine (not GitHub Actions, not a server). That weekly job is **not installed yet**.

The current snapshot (2026-10-01) was a manual run that stopped early: Amazon returned a CAPTCHA during the
deep check, so it has 67 topics but only 15 cards (`"stopped": "captcha"` in `manifest.json`).
Check `cards` and `stopped` in the manifest before relying on a snapshot.

## Data source

Parsed summaries of Amazon's public Kindle list pages (Best Sellers / Hot New Releases) and the search and
product pages for each deep-checked keyword. The data is produced by the step-01 script of
`clawcreek-howto-author` (`niche_research.py`), which is **not** in this repository; `manifest.json` records
the skill branch and commit used. This repository only holds the parsed output.

## Layout

This is the proposed layout for skill 1.2.0; the skill side may still adjust it.

```text
snapshot/
├── manifest.json                         # snapshot metadata (see below)
├── topics.json                           # aggregated hot-list result; same as topics.json from the skill's step 01
└── cards/
    └── <keyword-slug>/
        ├── card.md                       # topic card for one keyword
        └── competitors.json              # competitor data for that keyword
```

`snapshot/manifest.json` fields:

| Field          | Meaning                                                        |
|----------------|----------------------------------------------------------------|
| `schema`       | Schema version, e.g. `kindle-niche-snapshot/0.1`               |
| `generated_at` | Generation time, UTC ISO 8601 (e.g. `2026-10-05T00:00:00Z`); `null` before the first snapshot |
| `topics`       | Number of topics in `topics.json`                              |
| `cards`        | Number of keyword directories under `cards/`                   |
| `files`        | Map of path (relative to `snapshot/`) → sha256 hex digest      |
| `skill`        | Repo, script path, branch and commit of the generating skill   |
| `requests`     | Total Amazon requests made by the run                          |
| `elapsed_seconds` | Total run time                                              |
| `steps`        | Requests and seconds per step (`topics`, `deep`)               |
| `stopped`      | `null` for a complete run, otherwise why it stopped early (e.g. `captcha`) |

## Rules

- Only parsed results. No raw Amazon HTML, ever.
- No credentials, cookies, or personal data.
- Every file must be 5 MB or smaller.

## Usage

The data in this repository is provided solely for use by the `clawcreek-howto-author` skill.
No open-source license is granted.
