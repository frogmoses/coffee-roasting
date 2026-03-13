# Coffee Roasting: AI Agent Reference

## CRITICAL: Security Protocol for Development

**BEFORE writing any code that requires credentials, API keys, or environment variables:**

1. 🔴 **MANDATORY**: Read `~/ClaudeWorkspace/.claude/docs/code-secure.md` completely
2. 🔴 **MANDATORY**: Follow the code-secure.md checklist exactly
3. 🔴 **MANDATORY**: Create `.env.example` file (never actual `.env`)
4. 🔴 **MANDATORY**: Create wrapper script first
5. 🔴 **MANDATORY**: Python code NEVER loads .env files (only `os.environ.get()`)
6. 🔴 **MANDATORY**: You must never access .env files in any way.

**Failure to follow this protocol is a security violation.**

See `~/ClaudeWorkspace/.claude/docs/code-secure.md` for complete implementation details.

## Running

Always use the wrapper script (injects secrets):
```bash
run_roast-analyzer analyze.py <command>
```

## Project Structure

```
coffee-roasting/
├── analyze.py              # CLI entry point (argparse dispatch)
├── roast_parser.py         # .alog file parsing via ast.literal_eval()
├── roast_metrics.py        # Metric extraction, target definitions, comparison
├── roast_analysis.py       # Recommendation engine (4 categories)
├── roast_display.py        # Terminal formatting with Unicode box-drawing
├── coffee_lookup.py        # find-coffee API client with auto server lifecycle
├── sentinel_loader.py      # Sentinel JSON loading, date-matching, visual extraction
├── pyproject.toml          # Package config (requires-python >=3.10, dep: requests)
├── roast-logs/             # .alog and .png files from Artisan (gitignored)
├── roast_history.json      # Persistent analysis results (gitignored)
└── reference/              # Hottop PDF manuals (gitignored)
```

## CLI Command -> Code Mapping

Dispatch table in `analyze.py:369-378`. Each command maps to a `cmd_*` function:

| Command | Function | Key flow |
|---------|----------|----------|
| `full` | `cmd_full()` `:243` | `cmd_scan()` -> `display_roast_summary()` -> `display_bean_profile()` -> `display_target_comparison()` -> `display_recommendations()` -> `display_next_roast()` -> `display_trend()` |
| `scan` | `cmd_scan()` `:94` | `scan_roast_logs()` -> `parse_alog()` -> `extract_roast_data()` -> `lookup_bean()` -> `match_sentinel_to_roast()` -> `analyze_roast()` -> `save_history()` |
| `show` | `cmd_show()` `:149` | `resolve_roast_id()` -> `display_roast_summary()` -> `display_bean_profile()` |
| `compare` | `cmd_compare()` `:167` | `compare_roasts()` -> `display_roast_comparison()` |
| `recommend` | `cmd_recommend()` `:195` | `display_target_comparison()` -> `display_recommendations()` -> `generate_next_roast_summary()` -> `display_next_roast()` |
| `cupping` | `cmd_cupping()` `:221` | Read/write `cupping_notes` in history |
| `list` | `cmd_list()` `:292` | `get_sorted_analyses()` -> `display_roast_list()` |
| `bean` | `cmd_bean()` `:299` | `lookup_bean()` -> `extract_bean_profile()` -> `display_bean_profile()` |

CLI flags: `--force` (scan/full), `--verbose/-v` (recommend/full), `--notes/-n` (cupping).

Roast ID resolution (`resolve_roast_id()` `:64`): exact match -> batch number -> partial name (case-insensitive).

## Data Flow

```
.alog file
  -> roast_parser.parse_alog()           # ast.literal_eval() -> raw dict
  -> roast_parser.extract_roast_data()   # pull fields, decode events
  -> roast_metrics.extract_metrics()     # calculate phase %, temps, RoR, heat changes
  -> roast_metrics.add_visual_metrics()  # merge sentinel data if available
  -> roast_metrics.compare_to_targets()  # compare against TARGETS dict
  -> roast_analysis.generate_recommendations()  # 4 rec categories
  -> roast_analysis.generate_next_roast_summary()  # 2-4 action items
  -> roast_display.*                     # Unicode box-drawing output
  -> roast_history.json                  # persisted to disk
```

Parallel enrichment during scan:
- `coffee_lookup.lookup_bean()` — queries find-coffee API for bean profile
- `sentinel_loader.match_sentinel_to_roast()` — finds visual data by date match

## Target Constants

Defined in `roast_metrics.py:10` as the `TARGETS` dict:

| Metric | Target | Tolerance/Range | Key |
|--------|--------|-----------------|-----|
| Drying phase | 45% | +/- 3% | `dry_phase_pct` |
| Maillard phase | 40% | +/- 3% | `mid_phase_pct` |
| Development phase | 15% | +/- 2% | `dev_phase_pct` |
| Total time | 675s (11:15) | +/- 30s | `total_time` |
| Turning point BT | 140-150F | range | `tp_bt` |
| First crack BT | 358-362F | range | `fc_bt` |
| Drop BT | 375-380F | range | `drop_bt` |
| RoR at FC | 12-14 F/min | range | `ror_at_fc` |
| Heat adjustments | max 4 | hard max | `heat_adjustments` |

Comparison status values: `"OK"`, `"!! HIGH"`, `"!! LOW"`.

## Recommendation Engine (`roast_analysis.py`)

`generate_recommendations()` `:44` produces recs from 4 categories:

1. **Roast mechanics** (`_mechanic_recommendations` `:81`) — phase timing, heat control, RoR, temperatures
2. **Bean-specific** (`_bean_recommendations` `:305`) — flavor profile advice based on find-coffee data
3. **Flavor gap** (`_flavor_gap_recommendations` `:382`) — professional cupping notes vs actual results
4. **Visual** (`_visual_recommendations` `:418`) — sentinel development scores

### Recommendation dict fields

Each rec is a dict with:
- `priority`: 1 (fix first), 2 (worth improving), 3 (info)
- `category`: display category string (e.g. "RoR Control", "Temperature")
- `text`: default display text (truncated cupping notes for Flavor Goal recs)
- `full_text` (optional): full-length text, present on Flavor Goal recs — shown when `--verbose` flag is used

### Beginner-friendly features

- **Actionable temperature recs**: `fc_bt` and `drop_bt` recs explain what the temperature means and what to do
- **RoR linking**: when both RoR oscillation and low FC RoR recs are present, a post-pass (`roast_analysis.py:288-300`) appends a linking sentence
- **Cupping notes truncation**: Flavor Goal recs truncate professional notes to 2 sentences; full text via `--verbose`
- **Priority legend**: displayed at top of recommendations box (`roast_display.py:248`)

### Next Roast Synthesis

`generate_next_roast_summary()` `:514` maps off-target comparisons to concrete actions:

| Pattern | Action |
|---------|--------|
| Long drying / low TP | "Charge hotter" |
| RoR oscillating / too many heat changes | "Plan deliberate heat cuts" |
| Low drop temp | "Run longer after FC" |
| Low FC temp | "Maintain heat through Maillard" |
| High FC RoR | "Cut heat earlier" |
| Low FC RoR | "More momentum into FC" |
| High drop temp | "Drop sooner" |

Deduplicates via `seen` set keyed on action theme. Caps at 4 items.

## Display Layer (`roast_display.py`)

Box width: 72 for recommendations/comparisons/next-roast, 62 for summaries/trends.

Key functions:
- `display_roast_summary()` `:54` — temps, phases, RoR, visual scores, cupping notes
- `display_bean_profile()` `:137` — cupping notes, flavor bars, cupping chart scores
- `display_target_comparison()` `:192` — metric vs target table
- `display_recommendations()` `:229` — priority legend + wrapped rec text; uses `full_text` when `verbose=True`
- `display_next_roast()` `:289` — numbered action items
- `display_roast_comparison()` `:333` — side-by-side delta table with improved/regressed
- `display_trend()` `:388` — all roasts in a compact metric table
- `display_roast_list()` `:427` — batch #, date, title, time, drop temp

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

Built in `roast_parser.py:69`: `{batch_nr}_{title}_{roastisodate}` (e.g., `1_Ethiopia Gerba Hechere_2026-02-06`).

### Computed fields used

Extracted in `roast_metrics.extract_metrics()` `:143`:
- Phase times: `totaltime`, `dryphasetime`, `midphasetime`, `finishphasetime`
- Temperatures: `CHARGE_BT`, `CHARGE_ET`, `TP_BT`, `TP_time`, `DRY_BT`, `FCs_BT`, `FCs_time`, `DROP_BT`, `DROP_time`, `MET`
- RoR: `fcs_ror`, `dry_phase_ror`, `mid_phase_ror`, `finish_phase_ror`, `total_ror`
- Deltas: `dry_phase_delta_temp`, `mid_phase_delta_temp`, `finish_phase_delta_temp`
- Other: `AUC`, `weightin`, `weightout`, `weight_loss`

### Extracted roast data fields

`extract_roast_data()` (`roast_parser.py:35`) also pulls: `title`, `roastbatchnr`, `weight`, `machinesetup`/`roastertype`, `mode` (F/C), `roastingnotes`, `cuppingnotes`, `flavors`/`flavorlabels`, `heavyFC`, `lowFC`, `oily`, `tipping`, `scorching`.

## find-coffee Integration

- API: `GET /api/purchased_coffees?name=<search>` — case-insensitive LIKE match
- Returns: cupping_notes, 12 flavor scores (floral, berry, citrus, honey, sugar, caramel, fruit, cocoa, nut, rustic, spice, body), 10 cupping chart scores (dry_fragrance, wet_aroma, brightness, flavor, body, finish, sweetness, clean_cup, complexity, uniformity)
- `coffee_lookup.py` checks if find-coffee is running, starts it via `FIND_COFFEE_WRAPPER` if not, queries, then kills the process in `finally` block (only if we started it)
- Fallback search: if no results, retries with first 2 words of the bean name (`coffee_lookup.py:140`)
- Env vars (all required for bean lookup to work, no defaults):
  - `FIND_COFFEE_URL` — API base URL (e.g., `http://localhost:5000`)
  - `FIND_COFFEE_WRAPPER` — path to wrapper script that starts the server
- If either env var is missing, bean lookup is silently skipped

## Sentinel Visual Integration

`sentinel_loader.py` scans directories listed in `SENTINEL_CAPTURES_DIRS` env var (colon-separated paths, no default). If unset, visual data is silently skipped.

Both [r1-eye](https://github.com/frogmoses/r1-eye) and [GoPro](https://github.com/frogmoses/gopro) sentinels produce identical session JSON formats (`sentinel_YYYY-MM-DD_HHMM.json`).

### Matching logic (`match_sentinel_to_roast` `:49`)

1. Extract date from sentinel `session_id` (first 10 chars)
2. Compare against `.alog` `roastisodate`
3. Multiple matches on same date: closest time wins (HHMM comparison)
4. Fallback: latest session on that date

### Visual metrics added to analysis

| Metric | Source | Description |
|--------|--------|-------------|
| `visual_development_scores` | `extract_visual_data()` | List of `{elapsed, score, phase}` trajectory points |
| `visual_final_score` | Last non-zero `development_score` | 1-10 scale |
| `visual_uniformity` | `_classify_uniformity()` | excellent, good, moderate, poor, unknown |
| `visual_score_count` | Count of scored captures | Number of trajectory points |
| `visual_final_color` | Last observation's `color_assessment` | Text description |

### Visual recommendation triggers (`_visual_recommendations` `:418`)

- Score plateau (3+ consecutive same score in maillard/development) -> increase heat
- Rapid score jump (delta >= 3) -> too aggressive heat
- Poor uniformity -> drum/charge issue
- High visual score (>=8) + short development (<14%) -> surface scorching

## History File

`roast_history.json` (gitignored) — keyed by roast ID. Each entry contains:
- `roast_id`, `title`, `roast_date`, `batch_nr`
- `metrics` dict (all extracted metrics)
- `comparisons` list (target comparison results)
- `recommendations` list (generated recs)
- `bean_profile` dict or null
- `cupping_notes`, `roasting_notes`
- `source_file` (path to .alog)

Loaded/saved by `load_history()`/`save_history()` in `analyze.py:45-54`.

## Coding Conventions

- No Python typing (per workspace CLAUDE.md)
- Always provide comments
- Use `uv` for package management (`uv add`, not pip)
- Secrets via `run_roast-analyzer` wrapper, never in code
