# Coffee Roasting with Hottop KN-8828B-2K+

Roast analysis tool for the Hottop KN-8828B-2K+ connected to Artisan. Analyzes `.alog` roast logs, looks up bean profiles, compares metrics against targets, and gives you actionable recommendations for the next roast.

## After a Roast

Save the `.alog` file from Artisan into `roast-logs/`, then:

```bash
run_roast-analyzer analyze.py full
```

This scans for new log files, shows a summary of the latest roast, compares it to targets, prints recommendations with a priority legend, and ends with a "Next Roast" box telling you exactly what to change.

## Setup

Requires Python 3.10+ and `uv`:

```bash
uv sync
```

All commands run through the wrapper script (handles secrets):

```bash
run_roast-analyzer analyze.py <command>
```

Optional integrations (configured via env vars in the wrapper script):

| Env var | Purpose |
|---------|---------|
| `FIND_COFFEE_URL` | find-coffee API URL for bean profile lookup |
| `FIND_COFFEE_WRAPPER` | Path to `run_find-coffee` script (auto-starts the server if needed) |
| `SENTINEL_CAPTURES_DIRS` | Colon-separated paths to r1-eye/GoPro capture directories for visual scoring |

All three are optional — features are silently skipped when unset.

## Commands

| Command | What it does |
|---------|-------------|
| `full [id]` | Scan + full report (summary, bean profile, targets, recommendations, next roast, trend) |
| `full [id] -v` | Same, with full professional cupping notes |
| `full --force` | Re-analyze all files first |
| `scan` | Ingest new `.alog` files into history |
| `scan --force` | Re-analyze everything from scratch |
| `show [id]` | Roast summary (temps, times, phases, RoR) |
| `compare [id1 id2]` | Side-by-side comparison of two roasts |
| `recommend [id] [-v]` | Target comparison + recommendations + next roast actions |
| `list` | All analyzed roasts with batch number, date, bean, metrics |
| `cupping <id> -n "notes"` | Attach tasting notes to a roast |
| `cupping <id>` | View existing cupping notes |
| `bean <name>` | Look up a bean in find-coffee |

All commands use `run_roast-analyzer analyze.py <command>`.

## Picking a Roast by ID

Most commands accept an optional roast identifier:

- **Batch number** — `3` (simplest, from `list`)
- **Partial name** — `ethiopia` (case-insensitive match)
- **Full roast ID** — `3_Ethiopia Gerba Hechere_2026-02-21`

If you don't specify an ID, the command uses the latest roast.

## What the Output Looks Like

**Recommendations** are prioritized:
- `[!!!]` — fix this first
- `[ ! ]` — worth improving
- `[   ]` — info

**Next Roast** box gives 2-4 concrete action items distilled from the analysis (e.g., "Charge hotter", "Plan 2-3 deliberate heat cuts").

**Trend** table shows key metrics across all roasts so you can see progress.

## Visual Data

If the [r1-eye](https://github.com/frogmoses/r1-eye) or [GoPro](https://github.com/frogmoses/gopro) sentinel was running during the roast, visual development data (color scoring, uniformity) is automatically matched by date and included in the analysis. No extra steps — just run `full --force` after sentinel captures have synced.

## Reference

- Hottop manuals: download from [Hottop USA](https://hottopusa.com/hottop-roasters.html) and place in `reference/`
- Roast logs: save `.alog` files from Artisan into `roast-logs/` (both directories are gitignored)
- Technical details: [CLAUDE.md](CLAUDE.md)
