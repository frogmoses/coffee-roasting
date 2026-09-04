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

Roaster side (the ear, see "Ear" below), on the roaster laptop:
```bash
run_ear ear.py listen --bean "<bean>" --record-only
```

## Project Structure

```
coffee-roasting/
├── analyze.py              # CLI entry point (argparse dispatch)
├── roast_parser.py         # .alog file parsing via ast.literal_eval()
├── roast_metrics.py        # Metric extraction, RoR analysis, curve series for LLM, SAFETY_EJECT_BT (no target bands)
├── roast_analysis.py       # Analysis orchestration, prior-roast selection, rec refresh, roast comparison
├── roast_narrative.py      # CHARGE->DROP control-timeline reconstruction (heater/fan moves)
├── cupping_intake.py       # Structured tasting questions (QUESTIONS, run_intake, intake_to_text, INTAKE_LEGEND)
├── roast_plan.py           # Contrast-set planner: same recipe through FC, varied FC->DROP seconds
├── llm_recommender.py      # LLM recommender: prompt assembly + claude-opus-4-8 structured call
├── hottop_reference.py     # Static Hottop control reference fed to the LLM prompt
├── roast_display.py        # Terminal formatting with Unicode box-drawing
├── coffee_lookup.py        # find-coffee API client with auto server lifecycle
├── sentinel_loader.py      # Session-JSON discovery/matching (shared), sentinel visual extraction
├── crack_loader.py         # Ear sidecar (crack_*.json) matching -> metrics["fc_audio"]
├── ear/                    # Roaster-side microphone first-crack detector (rsynced to the roaster)
│   ├── ear.py                  # CLI: listen | devices | show | tune
│   ├── crack_detector.py       # numpy click detector + FirstCrackTracker + DETECTOR_DEFAULTS
│   ├── audio_capture.py        # sounddevice stream -> queue -> WAV (stdlib wave)
│   ├── ear_session.py          # WS events, arming, sidecar, rsync, alert (sentinel.py analogue)
│   ├── artisan_sync.py         # Artisan WebSocket server (copy of gopro/artisan_sync.py)
│   ├── tune.py                 # Offline: WAV + .alog -> crack timeline, FC vs mark, sweeps
│   ├── alert.py / ear_display.py / fake_artisan.py / deploy.sh / ear.conf.example
│   └── captures/               # crack_*.json + crack_*.wav (gitignored)
├── .env.example            # Secret/env-var template (incl. ANTHROPIC_API_KEY); never commit .env
├── tests/                  # pytest suite (run: uv run pytest tests/)
├── pyproject.toml          # Package config (requires-python >=3.10, deps: requests, anthropic; dev: pytest)
├── log-sync/               # Artisan log sync scripts for roaster machine
│   ├── artisan-sync-watch.sh   # inotifywait watcher (systemd service)
│   ├── artisan-sync.sh         # rsync to dev machine
│   ├── artisan-sync.conf.example  # Config template (copy to .conf, fill in)
│   └── artisan-sync.service    # systemd user unit
├── docs/                   # Printable session sheets (rwanda-contrast-session.html; also published as an artifact)
├── roast-logs/             # .alog and .png files from Artisan (gitignored)
├── roast_history.json      # Persistent analysis results (gitignored)
└── reference/              # Hottop PDF manuals (gitignored)
```

## CLI Command -> Code Mapping

Dispatch table at the bottom of `analyze.py`. Each command maps to a `cmd_*` function:

| Command | Function | Key flow |
|---------|----------|----------|
| `full` | `cmd_full()` | `cmd_scan()` -> `display_roast_summary()` -> `display_bean_profile()` -> `display_recommendations()` -> `display_next_roast()` -> `display_trend()` |
| `scan` | `cmd_scan()` | `scan_roast_logs()` -> `parse_alog()` -> `extract_roast_data()` -> `lookup_bean()` -> `match_sentinel_to_roast()` -> `enrich_trajectory_with_temps()` -> `match_crack_to_roast()` -> `extract_audio_data()` -> `select_prior_roasts()` -> `analyze_roast()` -> `save_history()` |
| `show` | `cmd_show()` | `resolve_roast_id()` -> `display_roast_summary()` -> `display_bean_profile()` |
| `compare` | `cmd_compare()` | `compare_roasts()` -> `display_roast_comparison()` |
| `recommend` | `cmd_recommend()` | `display_recommendations()` -> `display_next_roast()` (recs + `next_roast` read from cached history) |
| `cupping` | `cmd_cupping()` | Read/write `cupping_notes` in history — free text (`--notes`), guided intake (`--intake`), or `--intake-json`; on write, `refresh_recommendations()` re-runs the LLM with the notes (fails soft, keeps cached recs) |
| `plan` | `cmd_plan()` | `build_contrast_plan()` -> `display_contrast_plan()`; deterministic, no LLM/network |
| `list` | `cmd_list()` | `get_sorted_analyses()` -> `display_roast_list()` |
| `bean` | `cmd_bean()` | `lookup_bean()` -> `extract_bean_profile()` -> `display_bean_profile()` |

CLI flags: `--force` (scan/full), `--verbose/-v` (recommend/full), `--notes/-n`, `--intake/-i`, `--intake-json` (cupping), `--dev` (plan; comma-separated FC->DROP seconds in roasting order, default `150,90,210`), `--debug` (global; print traceback on errors).

Roast ID resolution (`resolve_roast_id()`): exact match -> batch number -> partial name (case-insensitive, most recent roast wins on multiple matches).

Scan behaviors:
- A corrupt `.alog` is skipped with a warning instead of aborting the scan
- Roast ID collisions (same batch/title/date from a different file) get a `_HHMM` suffix instead of silently overwriting
- `--force` re-scan preserves `cupping_notes` (and the structured `cupping_intake`) previously added via the `cupping` command **and feeds them into the LLM prompt** (they take precedence over the .alog's Artisan-typed notes)
- `cmd_compare` errors on an unresolvable given ID instead of silently substituting the latest roasts (defaults to the two most recent only when IDs are omitted)
- `save_history()` writes atomically (temp file + `os.replace`)

## Data Flow

```
.alog file
  -> roast_parser.parse_alog()           # ast.literal_eval() -> raw dict
  -> roast_parser.extract_roast_data()   # pull fields, decode events
  -> roast_metrics.extract_metrics()     # phase %, temps, RoR, heat changes, fc_check (curve-detected FC)
  -> roast_metrics.add_visual_metrics()  # merge sentinel data if available (retired sentinels)
  -> roast_metrics.add_audio_metrics()   # merge the ear's fc_audio if a crack sidecar matched
  -> roast_narrative.build_control_timeline() # reconstruct heater/fan moves
  -> llm_recommender.generate_llm_recommendations()  # claude-opus-4-8 -> recs + next_roast
  -> roast_display.*                     # Unicode box-drawing output
  -> roast_history.json                  # persisted to disk (recs + next_roast cached)
```

There is no target-comparison step — numeric target bands were removed (see
"No Target Bands" below). Recommendations and next-roast actions are produced by
the LLM recommender **at scan time** and cached in `roast_history.json`.
The `recommend`/`full` commands display the cached output; `scan --force`
regenerates it, and — the important loop — `cupping --notes` regenerates it via
`roast_analysis.refresh_recommendations()`, so the model re-judges the roast
against how it actually tasted (flavor is the primary signal, and scan-time recs
predate cupping). If the API key or network is unavailable the recommender fails
soft — the metrics are still saved, recommendations are just empty (the scan log
prints the reason via `llm_status`; a failed cupping-refresh keeps the cached recs).

Parallel enrichment during scan:
- `coffee_lookup.lookup_bean()` — queries find-coffee API for bean profile
- `crack_loader.match_crack_to_roast()` + `extract_audio_data()` — the ear's microphone first-crack verdict (see "Ear" below) -> `metrics["fc_audio"]`
- `sentinel_loader.match_sentinel_to_roast()` — finds visual data by UUID (deterministic) or date/time (fallback)
- `sentinel_loader.enrich_trajectory_with_temps()` — adds BT/ET from .alog to each visual trajectory point

## No Target Bands

There are **no numeric target bands**. The previous `DEFAULT_TARGETS`/`TARGETS`
dict, the `targets.json` override file, and `compare_to_targets()` were removed.
Judging a roast against fixed phase-percentage / time / temperature ranges was
abandoned because the roaster has not yet dialed in a bean — those bands were
unvalidated theory-plus-limited-history guesses presented as ground truth, so a
deviation got stamped `!! HIGH`/`!! LOW` against a number that had no empirical
backing.

What replaces them:
- **Theory** (bean-agnostic, reliable regardless of any target): RoR shape
  (smooth decline, ever-decelerating bean temp per Rao, no crash/flick), phase
  balance, and the fact that drop temp and weight loss are *outcomes* of
  development time. This still lives in `roast_metrics.assess_ror_smoothness()`
  and in the LLM system prompt.
- **Flavor**: the bean's intended flavor profile (find-coffee) vs how it
  actually cupped (the roaster's own cupping notes). This is the real signal —
  the gap between intended and actual taste.
- The metrics themselves (phase %, times, temps, RoR) are still extracted and
  shown as **facts** (in `display_roast_summary`), just not compared to bands.

**Path to grounded targets (future):** once the roaster has a roast they love,
*that* roast's curve becomes the reference ("match the one that tasted great"),
replacing theory guesses. Not yet implemented — would touch the cupping workflow
and history schema.

`SAFETY_EJECT_BT = 408` in `roast_metrics.py` is the one fixed reference kept —
a physical safety ceiling, not a taste target. The Hottop manual's 395F figure
is wrong; 408F is the real ejection point on this machine. (The BT display also
alerts at 356F = FC imminent.) Metrics of 0/-1 still mean "event not recorded".

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
- `heat_correlation`: "low_input" (≤4 heat changes) or "high_input" (≥5)
- `fc_crash`, `fc_flick`: booleans; `crash_min_ror`: post-FC RoR minimum when crashed
- `ror_rising`: boolean — Maillard RoR climbed instead of declining (Rao's 2nd rule); `ror_rise`: magnitude of the sustained Maillard climb in F/min
- `ror_min`, `ror_max`, `ror_mean`: RoR range stats
- `details`: human-readable summary string

`extract_metrics()` computes `heat_adjustments` first, then passes the count to `assess_ror_smoothness()`. Weight loss is zeroed when `weightout` is 0 (Artisan reports a garbage 100%).

**Curve-detected first crack** (`detect_fc_from_curve(data)`): first crack releases steam and the RoR drops 3-7 F/min within ~20s of onset (seen on all 13 logged roasts). The detector finds the steepest `FC_DETECT_DROP_SPAN` (20s) RoR drop while BT is inside `FC_DETECT_BT_BAND` (350-372F; a prior — every by-ear mark on this machine fell 358-366F, and outside the band the ~380F late-dev nosedive looks identical) and reports `offset` = detected minus marked FC seconds, `mark_suspect` when |offset| > `FC_MARK_TOLERANCE` (30s). Uses the same interval-derived BT smoothing and 30s RoR window as the rest of the RoR analysis, plus a ~10s smoothing of the RoR itself. Validated on this roaster's logs: median offset +4s, 85% within 30s — so it is a consistency check on a by-ear mark (the bean cracks quietly), not ground truth. Stored as `metrics["fc_check"]` (None if the curve never enters the band), shown as `FC by curve:` in the summary, as the `FCoff` column in `display_trend`, passed to the LLM as `fc_mark_check`, and carried into `select_prior_roasts()` as `fc_offset` so a systematic early/late mark is visible across roasts.

**Curve series for the LLM**: `build_curve_series(data, step=30)` downsamples the CHARGE→DROP curve into `{time, bt, ror, marker}` rows — one per ~30s plus one at each recorded phase boundary (CHARGE/DRY/FCs/DROP) — using the same interval-derived ~10s BT smoothing and ~30s RoR window as `assess_ror_smoothness()`. Rendered into the LLM prompt by `llm_recommender._curve_block()` so the model can verify the heuristic flags against the curve itself. `ror` is None until a full lookback window fits inside the roast.

## Recommendation Engine (LLM)

Recommendations are generated by an LLM, not a fixed-template engine. The old
template engine (per-metric `if HIGH/LOW: emit string` rules) was removed
because it could flag *what* was off target but never explain *how* to fix it
on this machine — it discarded the control timeline (the actual heater/fan
moves) and reduced it to a single `heat_adjustments` integer.

### Flow

`roast_analysis.analyze_roast()` builds metrics, then calls
`llm_recommender.generate_llm_recommendations(metrics, data, bean_profile,
cupping_notes=, warnings=, prior_roasts=)` `llm_recommender.py`. That function:

1. Reconstructs the CHARGE->DROP control timeline from `data` via
   `roast_narrative.build_control_timeline()` / `format_narrative()`.
2. Assembles the prompt: data-quality warnings from `validate_metrics()` (so
   the model hedges advice built on bad recordings), a curated metrics dict
   shown as *facts* (incl. `ror_diagnostics` from `ror_smoothness`;
   `heat_adjustments` is kept even at 0 — zero moves is a meaningful fact), a
   **downsampled BT/RoR curve** (`roast_metrics.build_curve_series()`, ~30s
   rows + phase-boundary rows, same smoothing as the RoR analysis — lets the
   model verify the heuristic crash/flick/rising flags against the curve
   itself), the control timeline, the bean's intended flavor profile,
   operator observations (Artisan's `heavy_fc`/`low_fc`/`oily`/`tipping`/
   `scorching` flags + `roasting_notes` intent), visual development, up to 3
   **previous roasts of the same bean** (key facts, the advice given after
   each, and how each cupped — from `roast_analysis.select_prior_roasts()`),
   and the operator's own cupping notes. **No target bands** — the prompt
   explicitly tells the model there are no fixed numeric targets and to judge
   by theory + flavor instead.
3. Calls `claude-opus-4-8` with **structured output** (`output_config.format`
   with `_OUTPUT_SCHEMA`, effort `high`, adaptive thinking, non-streaming,
   `max_tokens=16000` — thinking counts against it; a `max_tokens` stop is
   reported as truncation, not a parse failure).
4. Returns `({"recommendations": [...], "next_roast": [...]}, status)` or
   `(None, status)` on any failure.

`analyze_roast()` stores `recommendations`, `next_roast`, and `llm_status` in
the analysis dict (persisted to history). The display layer is unchanged — the
model is instructed to emit the same rec shape it already consumed.

`roast_analysis.refresh_recommendations(analysis, history)` re-runs the same
call for an already-analyzed roast (re-parses `source_file` for the timeline
and curve, reuses cached metrics/bean profile/warnings, passes the history's
cupping notes). `cmd_cupping` calls it after saving notes — this closes the
flavor loop. Fails soft: on any error the cached recs stay untouched.

`roast_analysis.select_prior_roasts(history, roast_id, title, roast_date,
batch_nr, limit=3)` picks earlier roasts of the same bean (title match,
case-insensitive; ordered by (date, batch)) and compacts each to key metrics +
prior `next_roast` advice + cupping notes. This is the incremental path toward
"match the roast you loved" — the model sees the dial-in sequence, not one
roast in isolation.

### What the model is told (`_SYSTEM_PROMPT`, `hottop_reference.py`)

- Tie every rec to the actual control moves and to this machine's levers
  (heater %, fan %, timing of cuts relative to BT and FC). Name the dial and the
  moment ("ease the heater to ~80% by 250F BT"); don't say "charge hotter" when
  the timeline shows the heater was already maxed.
- There are no fixed numeric targets; judge two ways — (1) flavor: the bean's
  intended profile vs how it actually cupped, and (2) bean-agnostic theory
  (declining RoR, ever-decelerating bean temp, no crash/flick, phase balance).
  Drop temp and weight loss are *outcomes* of dev time; translate into
  time-after-FC or heat-timing changes. A metric value may be cited as a fact,
  not as conformance to a band.
- The RoR diagnostic flags are machine-tuned heuristics — check them against
  the provided BT/RoR curve and trust the curve when they disagree.
- Data-quality warnings mean the affected metrics are unreliable — hedge or
  skip advice that depends on them; if recording problems block analysis, make
  fixing the recording the top action.
- Previous roasts of the bean are the dial-in history — note whether earlier
  advice was applied and whether it worked (especially in the cup), and build
  on the roast-to-roast deltas instead of repeating generic advice.
- `hottop_reference.py:HOTTOP_CONTROLS` is prepended as machine background
  (heater/fan/drum/damper behavior, 340-345F cut point, 356F FC indicator,
  408F safety eject).

### Output contract (`_OUTPUT_SCHEMA`)

JSON schema enforced via structured output:
- `recommendations`: list of `{priority (1|2|3), category, text, full_text?}` —
  the exact dict shape `roast_display.display_recommendations()` renders
  (`full_text` shown under `--verbose`). Ordered most important first.
- `next_roast`: list of 2-4 short imperative action strings, rendered by
  `display_next_roast()`.

### Control timeline (`roast_narrative.py`)

`build_control_timeline(data)` walks the decoded `events` plus the `heater`/`fan`
profile arrays, bounded to CHARGE->DROP (so the post-drop cooling ramp and
sensor garbage are excluded), and returns `{moves, start_heater, start_fan,
phase_marks}`. Each move is `{rel_time, bt, control, percentage, marker}` where
`marker` annotates CHARGE/DRY/FCs/DROP. `format_narrative()` renders it as the
prompt text block (e.g. `6:31  BT 300F  Heater -> 90%   [DRY]`). This is the
input the old engine threw away.

### Failure modes (fail soft)

`generate_llm_recommendations()` returns `(None, status)` — never raises into the
scan — for: anthropic SDK not installed, no API credentials, API/network error,
model refusal, output truncated at `max_tokens`, or unparseable output.
`cmd_scan` prints the non-"ok" status as `!! recommendations skipped: <reason>`.
The metrics are still saved. A failed `refresh_recommendations()` (e.g. from
`cupping --notes`) keeps the previously cached recommendations and reports why.

## Cupping Intake & Contrast Planner

The operator is not a trained cupper and said so: free-text flavor-wheel notes
were coming back empty ("no discernible profile"). Two pieces replace vocabulary
with perception and experiment design:

**`cupping_intake.py`** — `QUESTIONS` is an ordered list of {key, prompt,
choices}: brew method, rest days, `balance` (-2 sour .. +2 roasty; the
development axis), `astringency` (0-3), `sweetness` (0-3), `body` (-1..1),
`preference` vs previous batch (worse/same/better/na), `drink_again`
(yes/meh/no), free `notes`. `run_intake(ask, say)` prompts on the terminal
(blank skips, invalid re-asks); `normalize_intake(dict)` validates JSON/script
input (accepts values, 1-based menu numbers, or label prefixes);
`intake_to_text()` renders one paragraph. `cmd_cupping` stores the dict as
`cupping_intake` in history AND writes the rendered paragraph to
`cupping_notes`, so the LLM loop and `select_prior_roasts()` need no schema
change. `INTAKE_LEGEND` is appended to the LLM system prompt so the model reads
the axes as roast mechanics (sour = under-developed, roasty = over, astringent =
uneven/scorched) and weights the preference answers above descriptors.

**`roast_plan.py`** — `build_contrast_plan(history, roast_id, dev_times)`
takes the anchor roast's actual control moves (re-parses `source_file`,
splits them at FC into shared pre-FC moves and "FC+Ns" post-FC moves), pools
FC time/BT stats over every roast of the same title, measures the same-day
batch-position FC shift (drum heat-soak), and projects the drop BT for each
FC->DROP duration from the bean's measured post-FC rise (fallback
`DEFAULT_POST_FC_RISE_F_PER_MIN = 9.5`, measured Aug 2026), flagging any batch
within `EJECT_MARGIN_F` of `SAFETY_EJECT_BT`. `dev_times` order = roasting
order; default `(150, 90, 210)` puts the control first so it is roasted under
the same cold-drum conditions as history's first-of-day batches. The only
lever varied is FC->DROP seconds because timing from FC insulates the
experiment from heat-soak drift (a warmer drum moves FC earlier, but "N seconds
after FC" still means the same thing). Deterministic — no LLM. Rendered by
`roast_display.display_contrast_plan()`.

Measured machine facts behind this (from roasts #8-#20): a single -10% heater
step shows up in ET only after ~60-70s and is within BT-RoR noise for 2+
minutes; identical manual schedules still spread FC by ~90s; later batches on
the same day reach FC 15-35s sooner.

## Ear: Microphone First-Crack Detector (`ear/`, `crack_loader.py`)

The current bean cracks quietly and the operator marks FCs by ear; every
development decision is timed from that mark. The curve check grades the mark
to ~±25 s after the fact. The ear hears the event itself. Item #2 of the agreed
automation sequence (curve check -> mic -> plant model -> optimizer).

**Where it runs.** `ear/` is rsynced verbatim to the roaster laptop
(`roaster`, coffee-man, Linux Mint 22.2, Python 3.12, uv, PipeWire, Artisan
4.0.0) as `~/CodeProjects/ear/` by `ear/deploy.sh` (`--full` creates the venv:
numpy, sounddevice, websockets; needs `libportaudio2`). Run via `~/.local/bin/run_ear`,
which `set -a; source`s `~/CodeProjects/ear/ear.conf` (no secrets, so it lives
beside the code like `log-sync/artisan-sync.conf`; template
`ear/ear.conf.example`) and execs `.venv/bin/python` — code reads only
`os.environ`. Both were created over ssh on 2026-09-04.
Mic: Smart Clippy EM272Z1 (plug-in-power capsule, TRS plug) through a Sound
Blaster Play! 3 USB card (ALSA card 1 "S3") — in the card's **4-pole headset
jack**; the mono mic jack reads a dead -63 dBFS floor with this capsule.
Capture gain is `amixer -c 1 cset numid=4 45,45` (+22.5 dB); at 100% finger
snaps clipped. `run_ear ear.py level` prints per-second RMS/peak/>3 kHz
share/cracks for jack and gain checks. PipeWire's default source is the laptop's
internal mic, so `EAR_DEVICE=Sound Blaster` selects the card by name. The ear
is now Artisan's WebSocket server on 8765 (the GoPro/R1 visual sentinels are
retired; don't run both).

**Session (`ear_session.py`).** Artisan connects on ON -> recording starts
(48 kHz mono int16 WAV via stdlib `wave`; ~86 MB per 15 min) so the drum/fan
floor is established before CHARGE. Events give the roast clock (CHARGE =
`charge_epoch`), arm the FC rule (DRY; fallback CHARGE+480 s or `--arm-at`),
mark DROP (sidecar saved + pushed; recording stops at DROP+`EAR_POST_DROP_S`
and the WAV is pushed), and OFF links the freshly written `.alog`
(`_link_alog`, with a retry until the file parses with a stable mtime) then
re-saves/pushes. Unlike the sentinel, the server stays up after DROP until OFF
or `EAR_OFF_TIMEOUT_S`, so the UUID link actually lands. `--record-only`
suppresses alerts (first sessions); `--record-now` starts recording without
Artisan and treats recording start as CHARGE (bench tests); an explicit
`--arm-at N` also lowers the rule's `min_elapsed_s` to N so `--arm-at 0`
bench runs can declare. `run_ear` lets variables already in the environment
override `ear.conf`, so inline overrides such as `EAR_CAPTURES_DIR=/tmp/x`
work. Ctrl-C finalizes.
Sidecar `ear/captures/crack_YYYY-MM-DD_HHMM.json`: session_id, bean, roast_uuid,
batch_nr, mode, artisan_events (lowercase, s since CHARGE), charge_epoch,
charge_source, armed {elapsed, source}, capture {device, sample_rate,
start_epoch, wav_file, duration_s, peak_dbfs, clipped_blocks, overflows},
detector (params), fc_rule, cracks [{epoch, stream_time, elapsed, peak_db,
dur_ms, flatness, armed}], fc_detected | null, notes.

**Detector (`crack_detector.py`, numpy only).** Per 100 ms block: 512-sample
frames at hop 128 (2.7 ms), Hann, batched rfft, band energy 3-12 kHz in dB
(motor/fan/harmonics live below ~2 kHz, cracks are broadband past 10 kHz).
Adaptive floor = 20th percentile of the last 2 s of frame levels, frozen while
a burst is open. Onset when level > floor + `thresh_db` (12). A burst is
accepted as a crack if 1 <= dur <= 30 ms and spectral flatness at its peak
>= 0.15 (rejects the Hottop's 356F panel beep, speech, fan surges, handling);
rejected long/tonal bursts blank onsets for 300 ms; onsets within 40 ms merge.
Reported onset = center of the first hot frame. `detect_cracks()` is the
offline wrapper over the same `ClickDetector.feed()` path (block-size parity
is tested). `FirstCrackTracker(n=7, window_s=20, min_elapsed_s=480)` declares
FC once when n accepted cracks fall in the rolling window (tuned 2026-09-04:
the drum makes 3-4 clicks/min at any heater setting, FC runs 20-30/min;
4-in-20s fired on that trickle 1-2 min early, 7-in-20s matched the curve
crash within 2-6 s on all three roasts); onset = first
crack in that window; pre-arm cracks are recorded but never counted
(drying-phase tumble). Second crack is not distinguished (declares once; SC is
minutes later). `DETECTOR_DEFAULTS` are starting points — `thresh_db`, `band`,
`max_dur_ms`, `min_flatness`, `n`/`window_s` must be tuned on real recordings
with `ear/tune.py` (crack histogram from CHARGE with DRY/FCs/DROP marks, FC
verdict vs mark, `--sweep thresh=8,10,12,14`, `--plot`). Known true-click
risk: heater relay clicks; the rate rule is the only live defense.

**Analyzer side.** `crack_loader.py` reads `CRACK_CAPTURES_DIR` at call time
(default `ear/captures` in this repo, where the roaster pushes),
matches by roastUUID then date/closest HH:MM (shared helpers
`sentinel_loader.find_session_logs` / `match_session_to_roast` /
`load_json_cached`), re-anchors elapsed from crack epochs and the `.alog`'s
`roastepoch` (`roast_parser` now exposes `roast_epoch`) if the WebSocket was
down, and reduces the sidecar to `metrics["fc_audio"]`: detected_time,
detected_bt, first_crack_time, mark_time, offset (detected - mark, same
convention and `FC_MARK_TOLERANCE` as `fc_check`), mark_suspect, crack_count,
cracks_after_arm, peak_cpm, capture stats, details. Shown as `FC by audio:` in
the summary under `FC by curve:`, passed to the LLM as `fc_audio_check` (prompt:
audio + curve agreeing within ~15 s is the true FC; prefer audio over the
by-ear mark; "not declared" with few cracks means the mic missed it), and
carried into prior roasts as `fc_audio_offset`.

**Phases.** A (built): record-only + sidecar + rsync + tune.py + analyzer
integration. B: tune defaults on the first recordings, add a real-FC excerpt
as a regression fixture. C: alerts on by default. D (future, `--push-artisan`,
default off): push FCs into Artisan — its push message format is ambiguous
between the docs page and `artisanlib/wsport.py`; verify before building.

**Tests.** `tests/test_ear_detector.py` (synthetic brown noise + hum with
injected 3 ms clicks, a 4 kHz beep, a 150 ms burst, a noise ramp; block-size
and int16/float parity; tracker rules) needs numpy (`pytest.importorskip`;
`uv sync --extra ear`). `tests/test_crack_loader.py` covers matching,
offsets, re-anchoring, metrics merge, prior roasts, and the scan seam.

## Display Layer (`roast_display.py`)

Box width: 72 for recommendations/comparisons/next-roast, 62 for summaries/trends.

Key functions:
- `_visual_summary()` — one-line trajectory interpretation (steady/stalled/rapid jump)
- `display_roast_summary()` — temps (+ CHARGE warning if `charge_bt` is missing), phases with time+RoR annotation, RoR, phase-grouped visual scores, cupping notes. The weight line shows `in -> out (X% loss)` once `weight_out` is entered, otherwise just `in`.
- `display_bean_profile()` — cupping notes, flavor bars, cupping chart scores
- `display_recommendations()` — priority legend + wrapped rec text; uses `full_text` when `verbose=True`
- `display_next_roast()` — numbered action items
- `display_roast_comparison()` — side-by-side delta table; direction is descriptive only (increased/decreased/unchanged), since with no target there's no "better/worse" verdict
- `display_trend()` — all roasts in a compact metric table
- `display_roast_list()` — batch #, date, title, time, drop temp
- `display_contrast_plan()` — printable contrast set: shared schedule, batches with projected drop BT, notes

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

Built in `roast_parser.py`: `{batch_nr}_{title}_{roastisodate}` (e.g., `1_Ethiopia Gerba Hechere_2026-02-06`).

### Computed fields used

Extracted in `roast_metrics.extract_metrics()`:
- Phase times: `totaltime`, `dryphasetime`, `midphasetime`, `finishphasetime`
- Temperatures: `CHARGE_BT`, `CHARGE_ET`, `TP_BT`, `TP_time`, `DRY_BT`, `FCs_BT`, `FCs_time`, `DROP_BT`, `DROP_time`, `MET`
- RoR: `fcs_ror`, `dry_phase_ror`, `mid_phase_ror`, `finish_phase_ror`, `total_ror`
- Deltas: `dry_phase_delta_temp`, `mid_phase_delta_temp`, `finish_phase_delta_temp`
- Other: `AUC`, `weightin`, `weightout`, `weight_loss`

### Extracted roast data fields

`extract_roast_data()` (`roast_parser.py`) also pulls: `title`, `roastbatchnr`, `roastUUID`, `weight`, `machinesetup`/`roastertype`, `mode` (F/C), `roastingnotes`, `cuppingnotes`, `flavors`/`flavorlabels`, `heavyFC`, `lowFC`, `oily`, `tipping`, `scorching`.

## find-coffee Integration

- API: `GET /api/purchased_coffees?name=<search>` — case-insensitive LIKE match
- Returns: cupping_notes, 12 flavor scores (floral, berry, citrus, honey, sugar, caramel, fruit, cocoa, nut, rustic, spice, body), 10 cupping chart scores (dry_fragrance, wet_aroma, brightness, flavor, body, finish, sweetness, clean_cup, complexity, uniformity)
- `coffee_lookup.py` checks if find-coffee is running, starts it via `FIND_COFFEE_WRAPPER` if not (on the port parsed from `FIND_COFFEE_URL`, default 5000), queries, then kills the process (only if we started it)
- Fallback search: if no results, retries with first 2 words of the bean name (`coffee_lookup.py`)
- Env vars (all required for bean lookup to work, no defaults):
  - `FIND_COFFEE_URL` — API base URL (e.g., `http://localhost:5000`)
  - `FIND_COFFEE_WRAPPER` — path to wrapper script that starts the server
- If either env var is missing, bean lookup is silently skipped

## Full Roasting Pipeline

This project is the analysis endpoint for a multi-machine pipeline. Three
paths carry data from the roaster laptop to this dev machine: Artisan roast
logs (always), the ear's crack sidecars + WAVs (live since Sept 2026), and the
visual sentinels' JSON (retired — the GoPro/R1 code paths still work, but
outdoor lighting made the scoring unreliable and nothing runs them now; the
ear owns Artisan's WebSocket port 8765).

### Pipeline overview

```
ROASTER MACHINE                              DEV MACHINE
─────────────────                            ───────────────
Artisan (.alog)                              coffee-roasting/roast-logs/
  │                                              ↑
  ├─ inotifywait ──→ artisan-sync.sh ──rsync─────┘
  │  (log-sync/, systemd user service)
  │
  └─ button events (ON/CHARGE/DRY/FCs/DROP/OFF)
       │ ws://127.0.0.1:8765
       ▼
Ear (~/CodeProjects/ear, run_ear)            coffee-roasting/ear/captures/
  mic → click detector → crack_*.json + .wav     ↑
  └─ rsync at DROP / OFF ──────────────────────┘
     OFF: reads newest .alog → roastUUID        crack_loader.py matches by UUID,
                                                else date/closest HH:MM
                                                → metrics["fc_audio"]

(retired) Sentinel JSON ── rsync ──→ gopro/captures/ or r1-eye/captures/
                                     sentinel_loader.py via SENTINEL_CAPTURES_DIRS
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

### Component 2: Sentinel visual capture (external projects — retired)

Two interchangeable camera systems produce identical sentinel JSON files during roasting. Both ran on the **roaster machine** alongside Artisan; neither is used now (see above), and they must not run while the ear is up (same port).

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

Sentinel JSON files are parsed once and cached by path+mtime (`_sentinel_cache`), since UUID matching scans every file per roast. `detect_plateau(trajectory, min_run=3)` is the shared stall detector used by `_visual_summary()` (display); the analysis-side visual reasoning now lives in the LLM prompt (visual trajectory + uniformity are passed via `llm_recommender._visual_block()`).

### Session matching logic (`sentinel_loader.match_session_to_roast`)

Shared by the sentinel loader and `crack_loader.py`; both session kinds name
files `<prefix>YYYY-MM-DD_HHMM.json` (`find_session_logs(dirs, prefix)`,
`load_json_cached(path)` with an mtime cache):

1. **UUID match (deterministic)**: if the `.alog` has a `roastUUID`, scan all session JSONs for a matching `roast_uuid` field — an exact 1:1 link
2. **Date match (fallback)**: `session_id[:10]` vs the `.alog` `roastisodate`
3. **Time tiebreak**: multiple matches on the same date → closest HH:MM wins (`roast_time[:5]`, since `.alog` times carry seconds)
4. **Last resort**: latest session on that date
5. If both gopro and r1-eye sessions exist for the same roast, whichever matches first wins — no merging

`match_sentinel_to_roast()` and `match_crack_to_roast()` are thin wrappers over this.

### Visual metrics added to analysis

| Metric | Source | Description |
|--------|--------|-------------|
| `visual_source` | `_infer_source_label()` | "GoPro", "r1-eye", or "Sentinel" (from file path) |
| `visual_development_scores` | `extract_visual_data()` | List of `{elapsed, score, phase, bt, et}` trajectory points |
| `visual_final_score` | Last non-zero `development_score` | 1-10 scale |
| `visual_uniformity` | `_classify_uniformity()` | excellent, good, moderate, poor, unknown |
| `visual_score_count` | Count of scored captures | Number of trajectory points |
| `visual_final_color` | Last observation's `color_assessment` | Text description |

Trajectory points are enriched with BT/ET from the `.alog` by `enrich_trajectory_with_temps()`. These temperatures are passed into the LLM prompt (`_visual_block()`) so visual advice has actionable BT context.

### Visual reasoning

Visual development is fed to the LLM recommender as part of the prompt
(`llm_recommender._visual_block()`): final score, uniformity, final color, and
the BT-enriched score trajectory. The model decides what (if anything) to
recommend from it — e.g. a score plateau (stall), a rapid score jump (too
aggressive), poor uniformity (drum/charge), or a high score with short
development (surface scorching). There is no separate template trigger function
anymore. `detect_plateau()` is still used display-side by `_visual_summary()`.

### Visual display features

- Timeline grouped by phase (Drying/Maillard/Development) instead of flat list
- Source label inferred from path ("GoPro" or "r1-eye") instead of hardcoded
- One-line interpretive summary via `_visual_summary()`: "Steady progression 2→8", "Stalled at 5 during maillard", "Rapid jump to 8 at 5:30"

## History File

`roast_history.json` (gitignored) — keyed by roast ID. Each entry contains:
- `roast_id`, `title`, `roast_date`, `batch_nr`
- `metrics` dict (all extracted metrics)
- `recommendations` list (LLM-generated recs; empty if the LLM was unavailable)
- `next_roast` list (2-4 LLM-generated action strings)
- `llm_status` string (`"ok"` or the reason recs were skipped)
- `bean_profile` dict or null
- `cupping_notes`, `roasting_notes`
- `cupping_intake` dict (only when entered via `cupping --intake`/`--intake-json`; preserved across `--force`)
- `warnings` list (data-quality warnings from `validate_metrics()`)
- `metrics["fc_check"]` (curve-detected FC vs mark) and `metrics["fc_audio"]` (ear verdict; only when a sidecar matched)
- `source_file` (path to .alog)

Loaded/saved by `load_history()`/`save_history()` in `analyze.py`.

## Coding Conventions

- No Python typing (per workspace CLAUDE.md) — the LLM structured-output schema
  uses a raw JSON Schema dict (`_OUTPUT_SCHEMA`), not Pydantic, to stay typing-free
- Always provide comments
- Use `uv` for package management (`uv add`, not pip)
- Secrets via `run_roast-analyzer` wrapper, never in code. `ANTHROPIC_API_KEY`
  (for the LLM recommender) and the find-coffee vars are injected by the wrapper
  and read only via the environment — the Anthropic SDK reads the key itself; no
  `.env` loading. See `.env.example`.
- Run tests with `uv run pytest tests/` — pure-function tests over synthetic
  roast curves; no network or real roast-logs needed. The scan/CLI tests stub
  `roast_analysis.generate_llm_recommendations` so they never call the API.
  `uv sync --extra ear` installs numpy/sounddevice so the ear detector tests
  run (they `importorskip` numpy otherwise). The analyzer itself stays
  numpy-free; only `ear/` uses numpy.
- Never give a non-secret settings file the dot-env extension (the ear's
  settings file is `ear.conf` for this reason): the secrets hook blocks any
  shell command whose text contains that extension, file names included.
