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
├── sentinel_loader.py      # Sentinel JSON loading, UUID/date matching, visual extraction
├── targets.json            # Optional per-key target overrides (not created by default)
├── tests/                  # pytest suite (run: uv run pytest tests/)
├── pyproject.toml          # Package config (requires-python >=3.10, dep: requests; dev: pytest)
├── log-sync/               # Artisan log sync scripts for roaster machine
│   ├── artisan-sync-watch.sh   # inotifywait watcher (systemd service)
│   ├── artisan-sync.sh         # rsync to dev machine
│   ├── artisan-sync.conf.example  # Config template (copy to .conf, fill in)
│   └── artisan-sync.service    # systemd user unit
├── roast-logs/             # .alog and .png files from Artisan (gitignored)
├── roast_history.json      # Persistent analysis results (gitignored)
└── reference/              # Hottop PDF manuals (gitignored)
```

## CLI Command -> Code Mapping

Dispatch table at the bottom of `analyze.py`. Each command maps to a `cmd_*` function:

| Command | Function | Key flow |
|---------|----------|----------|
| `full` | `cmd_full()` `:297` | `cmd_scan()` -> `display_roast_summary()` -> `display_bean_profile()` -> `display_target_comparison()` -> `display_recommendations()` -> `display_next_roast()` -> `display_trend()` |
| `scan` | `cmd_scan()` `:107` | `scan_roast_logs()` -> `parse_alog()` -> `extract_roast_data()` -> `lookup_bean()` -> `match_sentinel_to_roast()` -> `enrich_trajectory_with_temps()` -> `analyze_roast()` -> `save_history()` |
| `show` | `cmd_show()` `:198` | `resolve_roast_id()` -> `display_roast_summary()` -> `display_bean_profile()` |
| `compare` | `cmd_compare()` `:216` | `compare_roasts()` -> `display_roast_comparison()` |
| `recommend` | `cmd_recommend()` `:249` | `display_target_comparison()` -> `display_recommendations()` -> `generate_next_roast_summary()` -> `display_next_roast()` |
| `cupping` | `cmd_cupping()` `:275` | Read/write `cupping_notes` in history |
| `list` | `cmd_list()` `:346` | `get_sorted_analyses()` -> `display_roast_list()` |
| `bean` | `cmd_bean()` `:353` | `lookup_bean()` -> `extract_bean_profile()` -> `display_bean_profile()` |

CLI flags: `--force` (scan/full), `--verbose/-v` (recommend/full), `--notes/-n` (cupping), `--debug` (global; print traceback on errors).

Roast ID resolution (`resolve_roast_id()`): exact match -> batch number -> partial name (case-insensitive, most recent roast wins on multiple matches).

Scan behaviors:
- A corrupt `.alog` is skipped with a warning instead of aborting the scan
- Roast ID collisions (same batch/title/date from a different file) get a `_HHMM` suffix instead of silently overwriting
- `--force` re-scan preserves `cupping_notes` previously added via the `cupping` command
- `cmd_compare` errors on an unresolvable given ID instead of silently substituting the latest roasts (defaults to the two most recent only when IDs are omitted)
- `save_history()` writes atomically (temp file + `os.replace`)

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
- `sentinel_loader.match_sentinel_to_roast()` — finds visual data by UUID (deterministic) or date/time (fallback)
- `sentinel_loader.enrich_trajectory_with_temps()` — adds BT/ET from .alog to each visual trajectory point

## Target Constants

Defined in `roast_metrics.py` as `DEFAULT_TARGETS`; the active `TARGETS` dict merges optional per-key overrides from `targets.json` in the project root (no code change needed to recalibrate). Calibrated for the hot-charge regime (manual mode, ~300F charge, drop timed from FC) using roast history + theory (Hottop manual safety/FC points, Rao/Cropster RoR guidance, RoastRebels washed-African DTR).

| Metric | Target | Tolerance/Range | Key |
|--------|--------|-----------------|-----|
| Drying phase | 50% | +/- 5% | `dry_phase_pct` |
| Maillard phase | 32% | +/- 4% | `mid_phase_pct` |
| Development phase | 17% | +/- 3% | `dev_phase_pct` |
| Dev time after FC | 90-125s | range | `dev_phase_time` |
| Total time | 690s (11:30) | +/- 40s | `total_time` |
| Turning point BT | 150-170F | range | `tp_bt` |
| First crack BT | 356-366F | range | `fc_bt` |
| Drop BT | 374-388F | range (diagnostic) | `drop_bt` |
| RoR at FC | 14-18 F/min | range | `ror_at_fc` |
| Heat adjustments | max 4 | hard max | `heat_adjustments` |
| Weight loss | 13-16% | range (diagnostic) | `weight_loss_pct` |

`dev_phase_time` (seconds FC→DROP, Artisan's `finishphasetime`) is the actionable development lever; `drop_bt` and `weight_loss_pct` are treated as *outcomes* of dev time, not steering targets — recs translate their misses into time-after-FC adjustments. `weight_loss_pct` (roast/organic loss) is only compared when weight-out was entered (it's 0/skipped otherwise — see the extraction note below). `SAFETY_EJECT_BT = 395` (Hottop hard safety point; the machine also alerts at 356F = FC imminent).

Comparison status values: `"OK"`, `"!! HIGH"`, `"!! LOW"`. Metrics with value <= 0 (event not recorded; Artisan uses 0/-1) are skipped instead of flagged LOW — except `heat_adjustments`, where 0 is real. Seconds-based range targets display as M:SS.

## RoR Smoothness Analysis (`roast_metrics.py`)

`assess_ror_smoothness(data, heat_adjustment_count=0)` uses **phase-segmented oscillation counting**:

- **Drying phase (CHARGE→DRY)**: Skipped — TP recovery naturally causes direction changes
- **Maillard phase (DRY→FCs)**: Counted — this is where heat control matters most
- **Development phase (FCs→DROP)**: Counted normally
- **Fallback**: if `timeindex[1] == 0` (DRY not recorded), full CHARGE→DROP window with original thresholds

The ~30s RoR smoothing window is derived from the actual sampling interval (median of `timex` deltas), not a hardcoded point count.

**BT smoothing**: Artisan logs raw BT quantized to a coarse probe grid (~0.3-0.6F steps plus occasional spikes). Before any RoR is computed, BT is passed through a light centered moving average (~10s span, derived from the sampling interval via `smooth_half`) so the quantization staircase doesn't surface as phantom oscillation or false crash/flick. The ~10s smooth sits well under the 30s RoR window, so real crashes/flicks survive. All RoR analysis in `assess_ror_smoothness()` reads from this smoothed curve (`bt_s`); the displayed phase RoR figures come from Artisan's own `computed` fields and are unaffected.

Phase-segmented thresholds (lower since drying excluded): smooth ≤2, moderate 3-4, oscillating 5+.
Full-window fallback thresholds: smooth ≤3, moderate 4-6, oscillating 7+.

**FC crash/flick detection** (Rao/Cropster): within 90s after FCs, a crash = RoR falls ≥8 F/min from its FC value to below 5 F/min; a flick = RoR first sags ≥5 F/min below its FC value (`FLICK_MIN_SAG`), then climbs back ≥3 F/min off that minimum (`FLICK_MIN_REBOUND`). The sag gate is what keeps a gently wobbling, no-heat-input curve (e.g. FC 11 → 8 → 14) from being mis-flagged as a flick — the rebound alone is not enough. Heuristic thresholds tuned for this machine.

**Maillard deceleration check** (Rao's 2nd rule — "the bean temp shall always decelerate"): `_max_sustained_rise()` walks the Maillard (DRY→FCs) RoR series tracking the gain from the running trough to each later point; the biggest such trough→peak climb is the Maillard rise. `ror_rising` is set when that climb is ≥`DECEL_MIN_RISE` (4 F/min) **and** sustained ≥`DECEL_MIN_DURATION` (40s, past the ~30s RoR window) — so a brief blip or quantization wobble doesn't trip it. A continuously declining curve keeps setting new troughs, so the rise stays ~0. This is distinct from oscillation (wobble, which a monotonic *rising* curve would not register) and from the post-FC flick (a point event). Only computed in phase mode — the fallback (no DRY) can't exclude the drying climb, so `ror_rising` stays False there.

Return dict fields:
- `oscillations`: total direction changes (maillard + dev only, or full-window if fallback)
- `maillard_oscillations`, `dev_oscillations`: per-phase counts
- `severity`: "smooth", "moderate", "oscillating", or "unknown"
- `heat_correlation`: "low_input" (≤4 heat changes — within target) or "high_input" (≥5)
- `fc_crash`, `fc_flick`: booleans; `crash_min_ror`: post-FC RoR minimum when crashed
- `ror_rising`: boolean — Maillard RoR climbed instead of declining (Rao's 2nd rule); `ror_rise`: magnitude of the sustained Maillard climb in F/min
- `ror_min`, `ror_max`, `ror_mean`: RoR range stats
- `details`: human-readable summary string

`extract_metrics()` computes `heat_adjustments` first, then passes the count to `assess_ror_smoothness()`. Weight loss is zeroed when `weightout` is 0 (Artisan reports a garbage 100%).

## Recommendation Engine (`roast_analysis.py`)

`generate_recommendations()` `:85` produces recs from 4 categories:

1. **Roast mechanics** (`_mechanic_recommendations` `:122`) — root cause grouping, phase timing, heat control, context-aware RoR
2. **Bean-specific** (`_bean_recommendations` `:599`) — flavor profile advice based on find-coffee data
3. **Flavor gap** (`_flavor_gap_recommendations` `:676`) — professional cupping notes vs actual results
4. **Visual** (`_visual_recommendations` `:712`) — sentinel development scores with BT context

### Root cause grouping (`_mechanic_recommendations`)

Before per-metric recommendations, related off-target metrics are combined into single recs:

| Root cause | Trigger | Combined rec |
|------------|---------|-------------|
| Charge too cold | `tp_bt` LOW + `dry_phase_pct` HIGH | "Charge temp too low, stretched drying. Preheat more." |
| Insufficient momentum | `ror_at_fc` LOW + `fc_bt` LOW | "Not enough heat into FC. Maintain steady heat through Maillard." |
| Too much momentum | `ror_at_fc` HIGH + (`drop_bt` HIGH or `fc_bt` HIGH) | "Too much energy into/through FC. Cut heat ~340F, shorten time after FC." |
| Overdevelopment | `dev_phase_pct` HIGH + `drop_bt` HIGH | "Development ran long (X% / M:SS after FC) with high drop. Shorten time after FC ~15s." |
| Dev length | `dev_phase_time` + `dev_phase_pct` same direction | One rec keyed on the time lever (extend/shorten time after FC ~15s) |

Grouped metric keys go into a `handled` set; the per-metric loop skips anything already handled. The per-metric loop also handles `weight_loss_pct` (category "Development"): HIGH → roast went darker/drier, shorten time after FC or cut heat earlier; LOW → underdeveloped, run longer after FC. Framed as a development outcome (like `drop_bt`), not a direct lever. Rec text derives target numbers from `TARGETS` via `_target_str()` so text can't drift from the active targets. `tp_bt` has both LOW and HIGH handlers. `drop_bt` HIGH recs append a safety note when within 5F of `SAFETY_EJECT_BT`. Heat-cut advice points at ~340F (the Hottop manual recommends cutting heat/raising fan at 340-345F before FC).

### FC crash/flick recommendations

Generated from `fc_crash`/`fc_flick` before the oscillation block:
- Flick (priority 1): "RoR flicked back upward after first crack… never add heat during FC; plan one cut around 340-345F" — the char/smoky-ashy signature
- Crash without flick (priority 2): "RoR crashed… carry more momentum into FC, smaller/earlier pre-FC cut"

### Deceleration recommendation (Rao's 2nd rule)

When `ror_rising` is set, `_mechanic_recommendations` emits a priority-2 "RoR Control" rec before the oscillation block: RoR climbed ~X F/min through Maillard instead of declining → heat went in too late → apply more energy early (hotter charge / hold heat through drying) so the RoR peaks just after the turning point and falls into FC; avoid adding heat mid-Maillard. This is separate from the oscillation recs — a monotonic *rising* curve has few direction changes and would otherwise read as "smooth".

### Context-aware oscillation recommendations

RoR oscillation recs branch on `heat_correlation` from `assess_ror_smoothness()`:

| Heat correlation | Severity | Priority | Advice |
|-----------------|----------|----------|--------|
| `low_input` | moderate/oscillating | 3 (info) | Natural thermal behavior, hold heat steady longer |
| `high_input` | oscillating | 1 (fix first) | Reduce heat adjustment frequency and magnitude |
| `high_input` | moderate | 2 (improve) | Fewer, smaller adjustments |
| (missing/unknown) | any | 2 (improve) | Generic smooth-curve advice |

### Recommendation dict fields

Each rec is a dict with:
- `priority`: 1 (fix first), 2 (worth improving), 3 (info)
- `category`: display category string (e.g. "RoR Control", "Temperature")
- `text`: default display text (truncated cupping notes for Flavor Goal recs)
- `full_text` (optional): full-length text, present on Flavor Goal recs — shown when `--verbose` flag is used

### Beginner-friendly features

- **Actionable temperature recs**: `fc_bt` and `drop_bt` recs explain what the temperature means and what to do
- **RoR linking**: when both RoR oscillation and low FC RoR recs are present, a post-pass appends a linking sentence
- **Cupping notes truncation**: Flavor Goal recs truncate professional notes to 2 sentences; full text via `--verbose`
- **Priority legend**: displayed at top of recommendations box (`roast_display.py:361`)

### Next Roast Synthesis

`generate_next_roast_summary()` maps off-target comparisons to concrete actions:

| Pattern | Action |
|---------|--------|
| FC crash or flick | "Plan one heat cut around 340-345F, hold through first crack" |
| Rising Maillard RoR (`ror_rising`) | "Apply more heat early so RoR peaks after TP and declines" (skipped if a "charge hotter" action is already queued) |
| Long drying / low TP | "Charge hotter" |
| RoR oscillating + low_input heat correlation | "Hold heat steady longer between cuts" |
| RoR oscillating + high_input / too many heat changes | "Plan deliberate heat cuts" |
| Short dev (`dev_phase_time` LOW, `drop_bt` LOW, or `weight_loss_pct` LOW) | "Run 15-20s longer after first crack" |
| Low FC temp | "Maintain heat through Maillard (no cuts before 340F)" |
| High FC RoR | "Cut heat earlier, around 340F" |
| Low FC RoR | "More momentum into FC" |
| Long dev (`dev_phase_time` HIGH, `drop_bt` HIGH, or `weight_loss_pct` HIGH) | "Shorten time after first crack ~15s" |
| Poor visual uniformity | "Reduce batch size or preheat longer" |
| Visual development stalled | "Maintain heat through mid-roast" |

Deduplicates via `seen` set keyed on action theme. Caps at 4 items.

## Display Layer (`roast_display.py`)

Box width: 72 for recommendations/comparisons/next-roast, 62 for summaries/trends.

Key functions:
- `_visual_summary()` `:55` — one-line trajectory interpretation (steady/stalled/rapid jump)
- `display_roast_summary()` `:95` — temps (+ CHARGE warning if `charge_bt` is missing), phases with time+RoR annotation, RoR, phase-grouped visual scores, cupping notes. The weight line shows `in -> out (X% loss)` once `weight_out` is entered, otherwise just `in`.
- `display_bean_profile()` — cupping notes, flavor bars, cupping chart scores
- `display_target_comparison(comparisons, metrics=None)` — metric vs target table. When `metrics` is passed, phase rows (dry/mid/dev) get a `-> mm:ss at X F/min` sub-line so the headline percentage isn't the only signal (dry_phase_pct is a ratio and hides whether a miss is from phase duration or total-time denominator). Callers in `analyze.py` pass `analysis["metrics"]`.
- `display_recommendations()` — priority legend + wrapped rec text; uses `full_text` when `verbose=True`
- `display_next_roast()` — numbered action items
- `display_roast_comparison()` — side-by-side delta table with improved/regressed
- `display_trend()` — all roasts in a compact metric table
- `display_roast_list()` — batch #, date, title, time, drop temp

**Phase breakdown time/RoR annotation fields**: `dry_phase_time`/`dry_phase_ror`, `mid_phase_time`/`mid_phase_ror`, `dev_phase_time`/`dev_phase_ror` (all populated by `extract_metrics()` in `roast_metrics.py`). Note the development RoR field is `dev_phase_ror`, not `finish_phase_ror` — the internal Artisan field name is `finishphase` but the extracted metric is keyed `dev_phase_ror`.

**CHARGE data-quality warning**: `display_roast_summary()` surfaces `! CHARGE temperature not recorded - mark CHARGE manually next roast.` when `metrics["charge_bt"]` is 0/missing. This is in addition to the aggregate warnings at the top of the summary box. Does not mutate history — display-time only.

RoR smoothness line shows heat context: `moderate (natural curve variation)` for low-input, `moderate (3 heat changes)` for high-input/unknown. When `ror_rising` is set, a follow-up line flags it: `! RoR rising in Maillard (+X F/min) - should decelerate` (Rao's 2nd rule).

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

Extracted in `roast_metrics.extract_metrics()` `:328`:
- Phase times: `totaltime`, `dryphasetime`, `midphasetime`, `finishphasetime`
- Temperatures: `CHARGE_BT`, `CHARGE_ET`, `TP_BT`, `TP_time`, `DRY_BT`, `FCs_BT`, `FCs_time`, `DROP_BT`, `DROP_time`, `MET`
- RoR: `fcs_ror`, `dry_phase_ror`, `mid_phase_ror`, `finish_phase_ror`, `total_ror`
- Deltas: `dry_phase_delta_temp`, `mid_phase_delta_temp`, `finish_phase_delta_temp`
- Other: `AUC`, `weightin`, `weightout`, `weight_loss`

### Extracted roast data fields

`extract_roast_data()` (`roast_parser.py:35`) also pulls: `title`, `roastbatchnr`, `roastUUID`, `weight`, `machinesetup`/`roastertype`, `mode` (F/C), `roastingnotes`, `cuppingnotes`, `flavors`/`flavorlabels`, `heavyFC`, `lowFC`, `oily`, `tipping`, `scorching`.

## find-coffee Integration

- API: `GET /api/purchased_coffees?name=<search>` — case-insensitive LIKE match
- Returns: cupping_notes, 12 flavor scores (floral, berry, citrus, honey, sugar, caramel, fruit, cocoa, nut, rustic, spice, body), 10 cupping chart scores (dry_fragrance, wet_aroma, brightness, flavor, body, finish, sweetness, clean_cup, complexity, uniformity)
- `coffee_lookup.py` checks if find-coffee is running, starts it via `FIND_COFFEE_WRAPPER` if not (on the port parsed from `FIND_COFFEE_URL`, default 5000), queries, then kills the process (only if we started it)
- Fallback search: if no results, retries with first 2 words of the bean name (`coffee_lookup.py:151`)
- Env vars (all required for bean lookup to work, no defaults):
  - `FIND_COFFEE_URL` — API base URL (e.g., `http://localhost:5000`)
  - `FIND_COFFEE_WRAPPER` — path to wrapper script that starts the server
- If either env var is missing, bean lookup is silently skipped

## Full Roasting Pipeline

This project is the analysis endpoint for a multi-machine pipeline. Data flows from the roaster machine to this dev machine via two parallel paths: Artisan roast logs and sentinel visual captures.

### Pipeline overview

```
ROASTER MACHINE                           DEV MACHINE
─────────────────                         ───────────────
Artisan (.alog)                           coffee-roasting/roast-logs/
  │                                           ↑
  ├─ inotifywait ──→ artisan-sync.sh ──rsync──┘
  │  (log-sync/)
  │
  └─ OFF button ──ws──→ Sentinel reads .alog
                         extracts roastUUID + batch_nr

Sentinel (JSON)                           gopro/captures/ or r1-eye/captures/
  │                                           ↑
  └─ _push_log() ──────────────────rsync──────┘
     (in sentinel.py)                     sentinel_loader.py reads from here
                                          via SENTINEL_CAPTURES_DIRS env var
                                          matches by UUID (deterministic)
                                          or date/time (fallback)
```

### Component 1: Artisan log sync (`log-sync/`)

Runs on the **roaster machine** as a systemd user service. Watches for new/modified `.alog` and `.png` files and rsyncs them to the dev machine.

| File | Role |
|------|------|
| `artisan-sync-watch.sh` | inotifywait loop on `LOCAL_PATH`, triggers sync on `.alog`/`.png` changes (2s debounce) |
| `artisan-sync.sh` | rsync `.alog` + `.png` files to `REMOTE_USER@REMOTE_HOST:REMOTE_PATH` |
| `artisan-sync.conf.example` | Config template — copy to `artisan-sync.conf` and fill in SSH details |
| `artisan-sync.service` | systemd user unit to run the watcher |

Config vars (in `artisan-sync.conf`):
- `REMOTE_USER`, `REMOTE_HOST` — SSH target (dev machine)
- `REMOTE_PATH` — destination directory (e.g., `/path/to/coffee-roasting/roast-logs`)
- `LOCAL_PATH` — Artisan's save directory on the roaster
- `FILE_PATTERN` — `*.alog *.png`

### Component 2: Sentinel visual capture (external projects)

Two interchangeable camera systems produce identical sentinel JSON files during roasting. Both run on the **roaster machine** alongside Artisan.

| Project | Device | Capture method | Repo |
|---------|--------|---------------|------|
| [gopro](https://github.com/frogmoses/gopro) | GoPro Hero 13 | USB-C SDK HTTP commands | `~/CodeProjects/gopro` |
| [r1-eye](https://github.com/frogmoses/r1-eye) | Rabbit R1 (jailbroken) | ADB camera shutter | `~/CodeProjects/r1-eye` |

Both sentinels:
1. Run a WebSocket server (port 8765) that Artisan connects to as a client
2. Receive roast events (CHARGE, DRY, FCs, DROP, OFF, etc.) from Artisan button actions
3. Capture images at phase-adaptive intervals (drying: 30s, maillard: 20s, development: 10s)
4. Send each image to Claude Vision API for color/development scoring
5. On OFF event: read the newest `.alog` from `ARTISAN_SAVE_DIR` (default `~/coffee-roasts`) to extract `roastUUID` and `roastbatchnr` for deterministic linking
6. Save session data to `captures/sentinel_YYYY-MM-DD_HHMM.json`

**Artisan OFF button config**: Must have a WebSocket Command action: `send({"event": "OFF"})`. This triggers `.alog` linking — without it, sentinel falls back to date/time matching.

### Component 3: Sentinel log push to dev machine

Each sentinel has a `_push_log()` method that rsyncs the JSON file to the dev machine after DROP (or on Ctrl+C). This is best-effort — a failed push is non-fatal.

| Project | Env var | Example value |
|---------|---------|---------------|
| gopro | `SENTINEL_RSYNC_DEST` | `user@devmachine:~/CodeProjects/gopro/captures/` |
| r1-eye | `R1_PUSH_ADDRESS` | `user@devmachine:~/CodeProjects/r1-eye/captures/` |

r1-eye also has a manual fallback: `sync_captures.sh` pulls sentinel JSON/PNG files from the roaster via rsync (requires SSH alias "roaster" in `~/.ssh/config`).

### Component 4: Sentinel loader (`sentinel_loader.py`)

Reads sentinel JSON files from the dev machine and matches them to roast logs for analysis.

**Env var:** `SENTINEL_CAPTURES_DIRS` — colon-separated paths to sentinel capture directories on this machine. If unset, visual data is silently skipped.

Example: `SENTINEL_CAPTURES_DIRS=/home/brian/CodeProjects/gopro/captures:/home/brian/CodeProjects/r1-eye/captures`

### Sentinel JSON schema

Both projects produce identical JSON:

```json
{
  "session_id": "2026-02-28_1518",
  "bean_name": "Ethiopia Yirgacheffe",
  "roast_uuid": "d97e026e9c814453b8290999e3138e69",
  "batch_nr": 8,
  "artisan_events": {"charge": 0.0, "dry": 270.5, "fcs": 450.2, "drop": 570.8, "off": 580.0},
  "observations": [
    {
      "elapsed_seconds": 1.5,
      "phase": "drying",
      "type": "vision",
      "image_file": "captures/sentinel_20260228_151800.jpg",
      "color_assessment": "Pale green, raw unroasted beans",
      "development_score": 1,
      "uniformity": "Consistent color across all visible beans"
    }
  ]
}
```

`roast_uuid` and `batch_nr` are populated when the OFF event is received (Artisan saves the `.alog` on OFF, sentinel reads it). Empty/zero if OFF was not configured or not pressed.

Development score scale (1-10): green → pale yellow → tan → cinnamon → city → full city → dark → Vienna → French → Italian.

Sentinel JSON files are parsed once and cached by path+mtime (`_sentinel_cache`), since UUID matching scans every file per roast. `detect_plateau(trajectory, min_run=3)` is the shared stall detector used by both `_visual_summary()` (display) and `_visual_recommendations()` (analysis) so they always agree.

### Sentinel matching logic (`match_sentinel_to_roast` `:44`)

1. **UUID match (deterministic)**: if the `.alog` has a `roastUUID`, scan all sentinel JSONs for a matching `roast_uuid` field — this is an exact 1:1 link
2. **Date match (fallback)**: extract date from sentinel `session_id` (first 10 chars), compare against `.alog` `roastisodate`
3. **Time tiebreak**: multiple matches on same date → closest time wins (HHMM comparison)
4. **Last resort**: latest session on that date
5. If both gopro and r1-eye sessions exist for the same roast, whichever matches first (UUID or closest time) wins — no modality distinction or merging

### Visual metrics added to analysis

| Metric | Source | Description |
|--------|--------|-------------|
| `visual_source` | `_infer_source_label()` | "GoPro", "r1-eye", or "Sentinel" (from file path) |
| `visual_development_scores` | `extract_visual_data()` | List of `{elapsed, score, phase, bt, et}` trajectory points |
| `visual_final_score` | Last non-zero `development_score` | 1-10 scale |
| `visual_uniformity` | `_classify_uniformity()` | excellent, good, moderate, poor, unknown |
| `visual_score_count` | Count of scored captures | Number of trajectory points |
| `visual_final_color` | Last observation's `color_assessment` | Text description |

Trajectory points are enriched with BT/ET from the `.alog` by `enrich_trajectory_with_temps()` `:317`. These temperatures are included in visual recommendation text for actionable context.

### Visual recommendation triggers (`_visual_recommendations` `:712`)

- Score plateau (3+ consecutive same score in maillard/development) -> increase heat (includes BT if available)
- Rapid score jump (delta >= 3) -> too aggressive heat (includes BT if available)
- Poor uniformity -> drum/charge issue
- High visual score (>=8) + short development (<14%) -> surface scorching

### Visual display features

- Timeline grouped by phase (Drying/Maillard/Development) instead of flat list
- Source label inferred from path ("GoPro" or "r1-eye") instead of hardcoded
- One-line interpretive summary via `_visual_summary()`: "Steady progression 2→8", "Stalled at 5 during maillard", "Rapid jump to 8 at 5:30"

## History File

`roast_history.json` (gitignored) — keyed by roast ID. Each entry contains:
- `roast_id`, `title`, `roast_date`, `batch_nr`
- `metrics` dict (all extracted metrics)
- `comparisons` list (target comparison results)
- `recommendations` list (generated recs)
- `bean_profile` dict or null
- `cupping_notes`, `roasting_notes`
- `source_file` (path to .alog)

Loaded/saved by `load_history()`/`save_history()` in `analyze.py:47-54`.

## Coding Conventions

- No Python typing (per workspace CLAUDE.md)
- Always provide comments
- Use `uv` for package management (`uv add`, not pip)
- Secrets via `run_roast-analyzer` wrapper, never in code
- Run tests with `uv run pytest tests/` — pure-function tests over synthetic roast curves; no network or real roast-logs needed
