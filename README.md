# kindle-niche-snapshot

Weekly snapshot of Kindle nonfiction niche data, consumed by the `clawcreek-howto-author` skill.
The skill runs in a sandbox and downloads these files via their `raw.githubusercontent.com` URLs,
e.g. `https://raw.githubusercontent.com/clawcreek/kindle-niche-snapshot/main/snapshot/manifest.json`.

## Update frequency

Daily small batches, produced by `scripts/refresh.sh` on the maintainer's own machine (launchd, 03:00 local;
not GitHub Actions, not a server):

- The hot list is rebuilt from the Kindle list pages, which are refetched once a week (7-day cache).
- Each day deep-checks at most 12 due topics from the top 30 by heat (~250 requests, 8–12 s between pages,
  60 s between keywords). A topic is due when it has no card or its card is older than 7 days.
- A CAPTCHA stops the batch at once; the next day continues from the topic it stopped on.
- Cards accumulate across days; `card_index` in the manifest gives each card's fetch date.
- A day's batch is committed only if it added at least one card (`data: snapshot YYYY-MM-DD`).

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
| `requests`     | Amazon requests made by this run                               |
| `elapsed_seconds` | Run time of this run                                        |
| `steps`        | Requests and seconds per step of this run (`topics`, `deep`)   |
| `new_cards`    | Card slugs added by this run                                   |
| `stopped`      | `null`, or why this run's batch ended early and the next topic (`{"reason": "captcha", "next": "…"}`) |
| `card_index`   | slug → `{keyword, fetched_at}` for every card in `cards/`      |

## Rules

- Only parsed results. No raw Amazon HTML, ever.
- No credentials, cookies, or personal data.
- Every file must be 5 MB or smaller.

## Usage

The data in this repository is provided solely for use by the `clawcreek-howto-author` skill.
No open-source license is granted.
