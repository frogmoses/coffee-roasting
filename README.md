# Coffee Roasting with Hottop KN-8828B-2K+

Roast analysis tool for the Hottop KN-8828B-2K+ connected to Artisan. Analyzes `.alog` roast logs, looks up bean profiles, compares metrics against targets, and gives you actionable recommendations for the next roast.

All commands run from anywhere via the wrapper script:

```bash
run_roast-analyzer analyze.py <command>
```

## Quick Start — After a Roast

Save the `.alog` file from Artisan into `roast-logs/`, then:

```bash
run_roast-analyzer analyze.py full
```

This scans for new log files, shows a summary of the latest roast, compares it to targets, prints recommendations with a priority legend, and ends with a "Next Roast" box telling you exactly what to change.

## Commands

### Full analysis

```bash
run_roast-analyzer analyze.py full            # Scan + full report for the latest roast
run_roast-analyzer analyze.py full 3          # Full report for batch #3
run_roast-analyzer analyze.py full --force    # Re-analyze all files, then show latest
run_roast-analyzer analyze.py full -v         # Show full cupping notes (instead of summary)
```

Runs scan, then shows the summary, bean profile, target comparison, recommendations, "Next Roast" action plan, and trend (if you have multiple roasts). This is the command you'll use most.

### Get recommendations

```bash
run_roast-analyzer analyze.py recommend       # Recommendations for latest roast
run_roast-analyzer analyze.py recommend 3     # Recommendations for batch #3
run_roast-analyzer analyze.py recommend 3 -v  # Include full professional cupping notes
```

Shows target comparisons (how each metric measured against ideal), specific suggestions for what to change, and a "Next Roast" summary with 2-4 concrete action items.

Recommendations are prioritized:
- `[!!!]` — fix this first
- `[ ! ]` — worth improving
- `[   ]` — info

### Analyze new roasts

```bash
run_roast-analyzer analyze.py scan            # Ingest all new .alog files into history
run_roast-analyzer analyze.py scan --force    # Re-analyze everything from scratch
```

`scan` processes every `.alog` file in `roast-logs/` and saves each to history, skipping files already analyzed. You don't usually need to run this directly — `full` calls it automatically.

### View a roast

```bash
run_roast-analyzer analyze.py show            # Summary of latest roast
run_roast-analyzer analyze.py show 3          # Summary of batch #3
run_roast-analyzer analyze.py show ethiopia   # Partial name match works too
```

Shows the roast summary — temps, times, phase percentages, RoR. Includes bean profile if available.

### Compare roasts

```bash
run_roast-analyzer analyze.py compare         # Compare the last two roasts
run_roast-analyzer analyze.py compare 1 3     # Compare batch #1 vs batch #3
```

Side-by-side comparison showing what changed between roasts. Useful when you've adjusted technique and want to see the effect.

### List all roasts

```bash
run_roast-analyzer analyze.py list
```

Shows every analyzed roast with batch number, date, bean name, and key metrics. Use the batch numbers from this list with other commands.

### Add cupping notes

```bash
run_roast-analyzer analyze.py cupping 3 -n "bright berry, clean finish, slight caramel"
run_roast-analyzer analyze.py cupping 3       # View existing notes
```

Attach tasting notes to a roast after you've brewed and cupped it. Notes are saved in history alongside the analysis data.

### Look up a bean

```bash
run_roast-analyzer analyze.py bean "Ethiopia"
run_roast-analyzer analyze.py bean "Gerba Hechere"
```

Searches find-coffee for the bean and displays its flavor profile, cupping scores, and tasting notes. The tool auto-starts find-coffee if it's not running and shuts it down when done.

## Picking a Roast by ID

Most commands accept an optional roast identifier. You can use any of:

- **Batch number** — `3` (simplest, from `list`)
- **Partial name** — `ethiopia` (case-insensitive match)
- **Full roast ID** — `3_Ethiopia Gerba Hechere_2026-02-21` (from `list`)

If you don't specify an ID, the command uses the latest roast.

## Visual Data

If the [r1-eye](https://github.com/frogmoses/r1-eye) or [GoPro](https://github.com/frogmoses/gopro) sentinel was running during the roast, visual development data (color scoring, uniformity) is automatically matched by date and included in the analysis output. No extra steps needed — just run `full --force` after sentinel captures have synced.

By default, sentinel captures are loaded from `~/CodeProjects/r1-eye/captures/` and `~/CodeProjects/gopro/captures/`. Override with the `SENTINEL_CAPTURES_DIRS` environment variable (colon-separated paths).

## Reference

- Hottop manuals: `reference/Manual_BP-2K_v1-4web.pdf`, `reference/KN-8828B-2K+ Addendum Manual_0_1g.pdf`
- Roast logs: `roast-logs/` (`.alog` and `.png` files from Artisan)
- Technical details: [CLAUDE.md](CLAUDE.md) (source files, .alog format, integration internals)
