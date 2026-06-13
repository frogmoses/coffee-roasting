# Coffee Roasting with Hottop KN-8828B-2K+

Roast analysis tool for the Hottop KN-8828B-2K+ connected to Artisan. Analyzes `.alog` roast logs, looks up bean profiles, compares metrics against targets, and gives you actionable recommendations for the next roast.

## After a Roast

Artisan logs auto-sync from the roaster to `roast-logs/` via inotifywait + rsync (see `log-sync/` for the watcher scripts and systemd service). Then run:

```bash
run_roast-analyzer analyze.py full
```

This scans for new log files, shows a summary of the latest roast, compares it to targets, prints recommendations with a priority legend, and ends with a **Next Roast** box telling you exactly what to change.

> **Tip:** Enter the post-roast weight (weight-out) in Artisan before saving. When it's present, the report adds roast-loss analysis — see [Understanding Your Output](#understanding-your-output).

## Setup

Requires Python 3.10+ and `uv`:

```bash
uv sync
```

All commands run through the wrapper script (it injects secrets):

```bash
run_roast-analyzer analyze.py <command>
```

Optional integrations (configured via env vars in the wrapper script):

| Env var | Purpose |
|---------|---------|
| `FIND_COFFEE_URL` | find-coffee API URL for bean profile lookup |
| `FIND_COFFEE_WRAPPER` | Path to `run_find-coffee` script (auto-starts the server if needed) |
| `SENTINEL_CAPTURES_DIRS` | Colon-separated paths to r1-eye/GoPro capture directories for visual scoring |

All three are optional — the related features are silently skipped when unset.

## Commands

Every command is run as `run_roast-analyzer analyze.py <command>`. Most accept an optional roast ID (see [Picking a Roast by ID](#picking-a-roast-by-id)); without one they use the latest roast.

**Daily workflow**

| Command | What it does |
|---------|-------------|
| `full [id]` | Scan + full report (summary, bean profile, targets, recommendations, next roast, trend) |
| `full [id] -v` | Same, with full professional cupping notes |
| `show [id]` | Roast summary only (temps, times, phases, RoR, weight) |

**Re-analyze & inspect**

| Command | What it does |
|---------|-------------|
| `scan` / `scan --force` | Ingest new `.alog` files (or re-analyze everything from scratch) |
| `recommend [id] [-v]` | Target comparison + recommendations + next-roast actions |
| `compare [id1 id2]` | Side-by-side comparison of two roasts |
| `list` | All analyzed roasts with batch number, date, bean, metrics |

**Notes & lookup**

| Command | What it does |
|---------|-------------|
| `cupping <id> -n "notes"` | Attach tasting notes to a roast |
| `cupping <id>` | View existing cupping notes |
| `bean <name>` | Look up a bean in find-coffee |

Re-scanning with `--force` preserves any cupping notes you've added.

## Picking a Roast by ID

Most commands accept an optional roast identifier:

- **Batch number** — `3` (simplest, from `list`)
- **Partial name** — `ethiopia` (case-insensitive match, most recent wins)
- **Full roast ID** — `3_Ethiopia Gerba Hechere_2026-02-21`

If you don't specify an ID, the command uses the latest roast.

## Understanding Your Output

**Recommendations** are prioritized:
- `[!!!]` — fix this first
- `[ ! ]` — worth improving
- `[   ]` — info

**Phase Breakdown** and **Target Comparison** show each phase's raw time and RoR next to the percentage — e.g. `Drying: 61.6% (8:30 @ 26.8 F/min)` — so you can tell whether a high drying % is coming from a long phase or a short total time.

**Weight loss**: if you entered the post-roast weight in Artisan, the summary shows roast loss (`226g -> 192g (15.0% loss)`) and the target comparison flags whether it's in band. It's a diagnostic readout of development, not a separate lever — recommendations frame any miss as a change to time after first crack.

**RoR smoothness**: the summary rates the rate-of-rise curve (smooth / moderate / oscillating) with heat context. If the RoR *climbs* through Maillard instead of falling — a violation of Rao's rule that the bean temp should always decelerate — a line flags it (`! RoR rising in Maillard (+X F/min) - should decelerate`) and the recommendations tell you to get more heat in earlier so the curve peaks just after the turning point and declines into first crack.

**CHARGE warning**: if `charge_bt` wasn't recorded (missed/late CHARGE press), a warning line appears in the summary so you know to mark CHARGE manually next roast.

**Next Roast** box gives 2-4 concrete action items distilled from the analysis (e.g., "Charge hotter", "Hold heat steady longer between cuts").

**Trend** table shows key metrics across all roasts so you can see progress.

## Configuration

### Targets

Every metric is compared against a target band. The defaults are calibrated for a light-medium washed coffee on the hot-charge regime, but you can override any of them — **no code change** — by creating `targets.json` in the project root. List only the metrics you want to change; everything else keeps its default.

Targets come in three shapes. Match the shape of the metric you're overriding:

| Shape | Metrics | Fields to set |
|-------|---------|---------------|
| Range | `dev_phase_time`, `tp_bt`, `fc_bt`, `drop_bt`, `ror_at_fc`, `weight_loss_pct` | `min`, `max` |
| Target ± tolerance | `dry_phase_pct`, `mid_phase_pct`, `dev_phase_pct`, `total_time` | `target`, `tolerance` |
| Hard max | `heat_adjustments` | `max` |

Times are in **seconds** (`690` = 11:30); temperatures in °F.

Example `targets.json`:

```json
{
  "total_time": {"target": 740, "tolerance": 45},
  "dev_phase_time": {"min": 140, "max": 165},
  "drop_bt": {"min": 385, "max": 400}
}
```

After editing, re-scan so existing roasts are recompared against the new bands:

```bash
run_roast-analyzer analyze.py scan --force
```

A malformed `targets.json` silently falls back to the defaults, so if a change doesn't take effect, check the JSON.

## Visual Data

If the [r1-eye](https://github.com/frogmoses/r1-eye) or [GoPro](https://github.com/frogmoses/gopro) sentinel was running during the roast, visual development data (color scoring, uniformity) is automatically included in the analysis. No extra steps — just run `full --force` after sentinel captures have synced.

Sentinel sessions are linked to `.alog` files by UUID when Artisan's OFF button is configured to send `send({"event": "OFF"})` via WebSocket. This triggers the sentinel to read the `.alog` that Artisan just saved and embed the `roastUUID` for deterministic matching. Without OFF configured, matching falls back to date/time.

## Reference

- Hottop manuals: download from [Hottop USA](https://hottopusa.com/hottop-roasters.html) and place in `reference/`
- Roast logs: auto-synced from roaster to `roast-logs/` (gitignored); see `log-sync/` for setup
- Technical details for working on the code: [CLAUDE.md](CLAUDE.md)
