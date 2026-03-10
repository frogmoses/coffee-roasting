# Coffee Roasting: AI Agent Reference

See [README.md](README.md) for overview and usage commands.

## Running

Always use the wrapper script (injects secrets):
```bash
run_roast-analyzer analyze.py <command>
```

## Source Files

| File | Purpose |
|------|---------|
| `analyze.py` | CLI entry point (argparse subcommands: full, scan, show, compare, recommend, cupping, list, bean) |
| `roast_parser.py` | Parses `.alog` files via `ast.literal_eval()` — they're Python dict literals, not JSON |
| `roast_metrics.py` | Extracts metrics, defines targets, compares with status/deviation |
| `roast_analysis.py` | Rule-based recommendation engine, enriched by bean profile and visual data |
| `roast_display.py` | Terminal formatting with Unicode box-drawing |
| `coffee_lookup.py` | find-coffee API client with auto server lifecycle |
| `sentinel_loader.py` | Loads sentinel JSONs (r1-eye or GoPro format), matches to .alog by date, extracts visual metrics |
| `roast_history.json` | Persistent analysis results (auto-generated, gitignored) |

## Recommendation Engine (`roast_analysis.py`)

### Recommendation categories

1. **Roast mechanics** (`_mechanic_recommendations`) — phase timing, heat control, RoR, temperatures
2. **Bean-specific** (`_bean_recommendations`) — flavor profile advice based on find-coffee data
3. **Flavor gap** (`_flavor_gap_recommendations`) — professional cupping notes vs actual results
4. **Visual** (`_visual_recommendations`) — r1-eye / GoPro sentinel development scores

### Recommendation dict fields

Each rec is a dict with:
- `priority`: 1 (fix first), 2 (worth improving), 3 (info)
- `category`: display category string (e.g. "RoR Control", "Temperature")
- `text`: default display text (truncated cupping notes for Flavor Goal recs)
- `full_text` (optional): full-length text, present on Flavor Goal recs — shown when `--verbose` flag is used

### Beginner-friendly features

- **Actionable temperature recs**: `fc_bt` and `drop_bt` recs explain what the temperature means and what to do about it (not just "X vs target Y")
- **RoR linking**: when both RoR oscillation and low FC RoR recs are present, a post-pass appends a linking sentence explaining the connection
- **Cupping notes truncation**: Flavor Goal recs truncate professional cupping notes to 2 sentences by default; full text available via `--verbose`
- **Priority legend**: displayed at the top of the recommendations box
- **Next Roast synthesis**: `generate_next_roast_summary()` distills off-target metrics and priority recs into 2-4 concrete action items

### `generate_next_roast_summary(comparisons, metrics, recommendations)`

Maps off-target comparisons to concrete actions:
- Long drying / low TP → "Charge hotter"
- RoR oscillating / too many heat changes → "Plan deliberate heat cuts"
- Low drop temp → "Run longer after FC"
- Low FC temp → "Maintain heat through Maillard"
- High FC RoR → "Cut heat earlier"
- Low FC RoR → "More momentum into FC"
- High drop temp → "Drop sooner"

Deduplicates via `seen` set keyed on action theme. Caps at 4 items.

## Display Layer (`roast_display.py`)

- `display_recommendations(recs, verbose=False)` — shows priority legend, uses `full_text` when verbose
- `display_next_roast(actions)` — renders numbered action box from `generate_next_roast_summary()` output
- Box width: 72 for recommendations/comparisons, 62 for summaries/trends

## CLI Flags (`analyze.py`)

- `recommend` and `full` both accept `--verbose / -v` to show full cupping notes
- `scan` and `full` both accept `--force` to re-analyze all files
- Roast ID resolution: batch number, partial name match, or full roast ID

## .alog Technical Details

Artisan saves roast data as Python dict literals (not JSON). Parsed with `ast.literal_eval()`.

### Key fields

- `timeindex` = `[CHARGE, DRY_END, FCs, FCe, SCs, SCe, DROP, COOL]` as indices into `timex`; 0 means "not recorded" (except CHARGE)
- `temp2` = BT (bean temperature), `temp1` = ET (environment temperature)
- `specialeventstype`: 0=Fan, 1=Drum, 2=Damper, 3=Heater
- Event value decoding: `percentage = (value - 1) * 10`
- `extratemp1[0]` = heater profile, `extratemp2[0]` = fan profile
- `roastisodate` = ISO date string (e.g., "2026-02-06")
- `roasttime` = time string (e.g., "16:34")
- `computed` = Artisan's pre-calculated metrics (phase times, RoR, temps at events)

### Roast ID format

Built from `{batch_nr}_{title}_{roastisodate}` (e.g., `1_Ethiopia Gerba Hechere_2026-02-06`).

## find-coffee Integration

- API: `GET /api/purchased_coffees?name=<search>` returns JSON with cupping_notes, 12 flavor scores (floral, berry, citrus, etc.), 12 cupping chart scores (brightness, sweetness, clean_cup, etc.)
- `coffee_lookup.py` checks if find-coffee is running, starts it via `run_find-coffee -m find_coffee.cli web --port 5000 --no-debug` if not, queries, then kills the process in a `finally` block (only if we started it)
- App location: `~/CodeProjects/find-coffee`

## Sentinel Visual Integration

Visual data from sentinel systems flows into the analysis pipeline automatically. Both the [r1-eye](https://github.com/frogmoses/r1-eye) camera and [GoPro Hero 13](https://github.com/frogmoses/gopro) USB sentinel produce identical session JSON formats.

`sentinel_loader.py` scans both `~/CodeProjects/r1-eye/captures/` and `~/CodeProjects/gopro/captures/` by default. Override with `SENTINEL_CAPTURES_DIRS` env var (colon-separated paths).

Pipeline flow:

1. `sentinel_loader.py` scans all capture directories for `sentinel_*.json` files
2. Matches to `.alog` files by comparing `roastisodate` against the sentinel `session_id` date portion
3. `extract_visual_data()` pulls out development score trajectory, final score, and uniformity
4. `roast_metrics.add_visual_metrics()` merges into the metrics dict
5. `roast_display.py` renders a "Visual Development" section with score timeline
6. `roast_analysis._visual_recommendations()` generates advice based on:
   - Score plateaus (stalling) → increase heat
   - Rapid score jumps → too aggressive heat
   - Poor uniformity → drum/charge issue
   - High visual score + short development → surface scorching

### Visual metrics added to analysis

| Metric | Description |
|--------|-------------|
| `visual_development_scores` | List of `{elapsed, score, phase}` trajectory points |
| `visual_final_score` | Last non-zero development score (1-10 scale) |
| `visual_uniformity` | Rating: excellent, good, moderate, poor, unknown |
| `visual_score_count` | Number of scored captures |
| `visual_final_color` | Text description of final bean color |

## Coding Conventions

- No Python typing (per workspace CLAUDE.md)
- Always provide comments
- Use `uv` for package management (`uv add`, not pip)
- Secrets via `run_roast-analyzer` wrapper, never in code
