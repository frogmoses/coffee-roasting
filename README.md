# Coffee Roasting with Hottop KN-8828B-2K+

Roast analysis for a Hottop KN-8828B-2K+ run under Artisan. It reads the `.alog`
roast logs, looks up the bean, and tells you what to change on the next roast,
reasoning from roasting theory and how the coffee actually tasted rather than
from fixed target curves.

## After a Roast

Logs sync from the roaster on their own. Run:

```bash
run_roast-analyzer analyze.py full
```

You get, in the terminal: a summary of the latest roast (temps, phases, rate of
rise, where first crack landed by curve and by microphone), the bean's intended
flavor profile, prioritized recommendations, a **Next Roast** box with two to
four concrete changes, and a trend table across all roasts. Everything is saved
to `roast_history.json`, so later commands are instant.

> Before saving in Artisan, enter the post-roast weight and the ambient
> temperature (Roast Properties). The report then shows roast loss and an
> `Ambient:` line, and the advice can tell weather from technique when first
> crack drifts across sessions. A warm-up recorded as its own log goes in
> `roast-logs/not-roasts/` so it stays out of the history.

## After Tasting

This is the step that makes the advice good. Taste the roast, then answer a few
questions that need no flavor vocabulary:

```bash
run_roast-analyzer analyze.py cupping 21 --intake
```

It asks how you brewed it, sour-or-roasty, astringency, sweetness, body, and
whether it was better or worse than the last batch. The recommendations are
regenerated on the spot from how the coffee tasted. Free text works too with
`--notes "..."`.

Brew every batch you are comparing the same way. The Hario Switch recipe on
the session sheet: 15 g medium grind, 250 g water at 94°C, switch closed, pour
50 g and swirl at 0:00, fill to 250 g at 0:45, open at 2:00. Taste hot at about
four minutes and again ten minutes later; sourness, astringency, and whether
sweetness holds all show in the cooler cup. Rest three to five days first.

## On Roast Day

**Plan the batches.** Instead of repeating one recipe, roast a bracket:

```bash
run_roast-analyzer analyze.py plan
```

This prints a contrast set for the bean of your latest roast: the same moves
through first crack, then three batches that differ only in seconds from first
crack to drop, with projected drop temperatures and a safety-eject check. A
printable session sheet lives in `docs/rwanda-contrast-session.html`.

**Let the roaster listen for first crack.** On the roaster laptop, once, before
the first ON of the day:

```bash
run_ear ear.py listen --bean "Rwanda Rusizi Gaseke"
```

The ear records the roast from the microphone at the drum, logs every crack it
hears, and syncs the recording and a sidecar to this machine. When it counts
seven cracks inside 20 seconds it prints a bold FIRST CRACK line in its own
terminal and rings the bell. That alert lands 15 to 20 seconds after the burst
begins, so treat it as confirmation: mark FCs at the first distinct crack you
hear or at 363F, whichever comes first; mark immediately if the alert fires
before you have; and do not mark below about 355F unless it has. The ear
marks nothing in Artisan. On the next `full`, the summary shows **FC by
audio** next to your mark.

Leave the ear running all day. Restart Artisan and the Hottop between batches
as you normally do: the ear ignores the OFF from a restart, and after each
finished roast it starts a fresh session on its own. Press OFF in Artisan at
the end of a roast, then save the log; the ear waits up to five minutes for
that save and prints **Linked to .alog** when the recording is tied to it.
Ctrl-C stops the ear for the day. If the ear was started late, after CHARGE,
it still counts cracks and the analyzer rebuilds the roast clock from the log
afterwards. To check the mic and gain first:

```bash
run_ear ear.py level
```

Snap your fingers by the capsule: peaks should jump and the cracks column climb.

## Commands

All analyzer commands run as `run_roast-analyzer analyze.py <command>`. Most
take an optional roast ID (batch number, partial bean name, or full ID); without
one they use the latest roast.

| Command | What it does |
|---------|-------------|
| `full [id]` | Scan new logs, then the full report for one roast |
| `show [id]` | Summary only: temps, phases, RoR, first crack by curve and audio |
| `cupping <id> -i` | Guided tasting questions; regenerates recommendations |
| `cupping <id> -n "…"` | Free-text tasting notes; regenerates recommendations |
| `plan [id] --dev 150,90,210` | Contrast set for the next session (FC-to-drop seconds, roasting order) |
| `recommend [id] [-v]` | Recommendations and next-roast actions from the cache |
| `compare [id1 id2]` | Two roasts side by side |
| `list` | Every analyzed roast |
| `scan --force` | Re-analyze all logs (keeps your cupping notes) |
| `bean <name>` | Look up a bean in find-coffee |

Ear commands run on the roaster as `run_ear ear.py <command>`:

| Command | What it does |
|---------|-------------|
| `listen --bean X` | Record and log every roast of the day, with the terminal alert when first crack is declared |
| `listen --bean X --record-only` | Same without the alert |
| `listen --bean X --once` | Exit after one roast instead of waiting for the next |
| `level` | Per-second input level and crack count, for mic and gain checks |
| `devices` | List audio inputs |
| `show` | Summarize the latest sidecar |
| `tune FILE.wav --sidecar … --alog …` | Re-run the detector over a recording against the roast's marks |

## Understanding Your Output

**Recommendations** come from Claude reading the whole log: every heater and fan
move, the bean-temperature curve, anything you flagged in Artisan, up to three
earlier roasts of the same bean, and your tasting notes. They name the dial and
the moment. Priorities: `[!!!]` fix first, `[ ! ]` worth improving, `[   ]` info.

**Phase Breakdown** shows each phase's time and rate of rise beside its
percentage, e.g. `Drying: 46% (5:46 @ 26.8 F/min)`.

**First crack, three ways.** Your FCs mark, the curve's own estimate (`FC by
curve`, from the steam-release dip in the rate of rise), and the microphone's
(`FC by audio`). Each is shown as seconds before or after your mark, with a
`! check mark` flag when the gap is over 30 seconds. On a quiet bean the
microphone is the one to trust. `[offline]` after the audio line means the
verdict was rebuilt from the recording because the ear had no live roast
clock. `FC by audio: none (28 armed cracks, peak 18/min, rule 21)` means the
microphone heard cracks but never densely enough to declare; the peak against
the rule says how close it came.

**Ambient** is the air temperature you entered in Artisan. The roaster sits
outdoors until it gets cold, then in the garage, and on a fixed time-based
schedule a cooler day pushes first crack later, so the advice reads FC drift
across sessions in that light.

**RoR smoothness** rates the rate-of-rise curve, and flags a rising Maillard
RoR, which means heat went in too late.

**Weight loss** appears when you entered the post-roast weight. It is an
outcome of time after first crack, not a dial.

**CHARGE warning** means CHARGE was not marked; mark it manually next roast.

**Next Roast** lists the changes to make at the machine. **Trend** shows key
metrics for every roast, including how far each FC mark sat from the curve.

## How It Judges a Roast

There are no numeric target bands. Until you have roasted a bean you love, any
target curve is a guess, so the analysis reasons from two things: what the bean
is supposed to taste like versus how it cupped, and theory that holds for any
bean (a smoothly declining rate of rise, no crash or flick at first crack, a
sensible phase balance, drop temperature and weight loss as outcomes of
development time). The one fixed number is the 408°F safety eject. Once a roast
tastes right, its curve becomes the reference to match.

## Setup

**This machine.** Python 3.10+ and `uv`:

```bash
uv sync --extra ear
```

Commands run through the `run_roast-analyzer` wrapper, which injects these
environment variables:

| Env var | Purpose |
|---------|---------|
| `ANTHROPIC_API_KEY` | Required for recommendations; without it scans still record metrics |
| `FIND_COFFEE_URL`, `FIND_COFFEE_WRAPPER` | Optional bean-profile lookup via find-coffee |
| `CRACK_CAPTURES_DIR` | Where the ear pushes sidecars; defaults to `ear/captures` |
| `SENTINEL_CAPTURES_DIRS` | Optional: capture dirs of the retired GoPro/R1 visual sentinels |

**Roaster laptop.**

- Log sync: the scripts and systemd unit in `log-sync/` push `.alog` files to
  `roast-logs/` here as Artisan saves them.
- Ear: `DEPLOY_SSH_HOST=roaster ear/deploy.sh --full` installs the code and its
  venv (needs `libportaudio2`). Settings live in `~/CodeProjects/ear/ear.conf`,
  loaded by `~/.local/bin/run_ear`; template in `ear/ear.conf.example`. The
  EM272 capsule goes in the Sound Blaster's 4-pole headset jack. Set capture
  gain in `alsamixer -c 1` so the empty running drum peaks near -30 dBFS.
- Artisan: Config → Ports → WebSocket at `127.0.0.1:8765`, path `WebSocket`,
  with button actions `send({"event": "ON"})`, `CHARGE`, `DRY`, `FCs`, and
  `OFF`. OFF is what lets the ear link its recording to the saved `.alog`.
  The DROP button keeps its Hottop commands (flap, stirrer, heat, fan); the
  ear does not need a DROP event. The Hottop ignores Artisan until its own
  cool-down reaches the ready screen, so the usual practice is to shut both
  down and restart between batches to keep the drum hot; the ear tolerates
  that.

## Reference

- Hottop manuals: download from [Hottop USA](https://hottopusa.com/hottop-roasters.html) into `reference/`
- How the code works: [CLAUDE.md](CLAUDE.md)
