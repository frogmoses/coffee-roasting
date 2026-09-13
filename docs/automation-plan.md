# Automated Roast Control from Recommendations

**Status: on hold.** Nothing here gets built until there is a first good
reference roast: a batch that cupped the way the bean is meant to taste,
recorded in history with its cupping intake. The plan exists so the design
does not have to be rediscovered. Written 2026-09-13.

## Goal

The roaster should not watch a timer or click buttons during a roast. The
analyzer's recommendation for a bean becomes an Artisan alarm set; Artisan
executes it against the Hottop; the ear marks first crack; the drop is timed
from that mark. What stays manual: loading beans, pressing CHARGE, and
selecting the bean's alarm set.

This is automation of a *recommendation*, not playback of a past roast.
Playback replays one roast's moves by clock time; an alarm set encodes the
intended moves with BT triggers and guards, so it survives weather and
heat-soak drift and can carry a deliberate change.

## What was verified (Artisan 4.0.0 source, 2026-09-13)

**Alarms.** Triggers: a source (BT, ET, ΔBT, ΔET) compared `<`, `>`, `=`,
`≠` to a value, or a time in seconds after a From event (ON, START, CHARGE,
TP, DRY END, FC START, FC END, SC START, SC END, DROP, COOL END, or another
alarm). Guards: "If Alarm" (fires only after row N fired) and "But Not"
(only if row N has not). Each alarm fires once per roast. Actions: Pop Up,
Call Program, Event Button, Slider 1-4, the system events (START, DRY END,
FC START, FC END, SC START, SC END, DROP, COOL END, OFF, CHARGE), RampSoak,
PID, SV, Playback on/off, canvas color. Heater and fan are reached through
the sliders: on the roaster slider 4 is the Hottop heater and slider 1 the
fan (`slideractions=6, 0, 0, 5` in `Artisan.conf`). The DROP action runs the
DROP button, whose Hottop command is already
`heater(0);fan(10);motor(1);solenoid(1);stirrer(1)`.

**`.alrm` file.** JSON with parallel lists:

| key | encoding |
|---|---|
| `alarmflags` | 1 enabled / 0 disabled |
| `alarmguards`, `alarmnegguards` | 1-based row of the If Alarm / But Not guard, -1 none |
| `alarmtimes` | From event: 9 ON, -1 START, 0 CHARGE, 8 TP, 1 DRY END, 2 FC START, 3 FC END, 4 SC START, 5 SC END, 6 DROP, 7 COOL END, 10 If Alarm |
| `alarmoffsets` | seconds after the From event (0 = not time-triggered) |
| `alarmconds` | 0 `<`, 1 `>`, 2 `=`, 3 `≠` |
| `alarmsources` | -3 none, -2 ΔET, -1 ΔBT, 0 ET, 1 BT, 2+ extra devices |
| `alarmtemperatures` | trigger value |
| `alarmactions` | -1 none, 0 Pop Up, 1 Call Program, 2 Event Button, 3-6 Slider 1-4, 7 START, 8 DRY END, 9 FC START, 10 FC END, 11 SC START, 12 SC END, 13 DROP, 14 COOL END, 15 OFF, 16 CHARGE, 17/18 RampSoak on/off, 19/20 PID on/off, 21 SV, 22/23 Playback on/off, 24/25 canvas color |
| `alarmbeep` | 1 beep on trigger |
| `alarmstrings` | description; for Slider actions the value to set |

Loading: Config → Alarms → Load (`.alrm` or `.alog`), or automatically when
the bean's profile is the background and "load alarms from background" is
checked. Alarm Sets keep one table per bean inside Artisan's settings.

**First crack from the ear.** Artisan's WebSocket port accepts pushes from
the device server. Marking first crack:

```json
{"pushMessage": "addEvent", "data": {"event": "firstCrackBeginningEvent"}}
```

The message and event names are configurable in Config → Ports → WebSocket
(verify the exact strings on the roaster before building). `startRoasting`
marks CHARGE (with an optional START on CHARGE) and `endRoasting` marks DROP.
Artisan marks at the moment of the push; it cannot backdate to the onset.

**Hottop driver.** Artisan holds control by sending a frame every 0.3 s; if
the machine hears nothing for 1 s it ejects the beans. Control can be taken
whenever BT ≤ 220°C (428°F) and Artisan forces an emergency dump (heater 0,
fan 100, door open) at 220°C. Nothing in the driver waits for the Hottop's
ready screen.

**Between Batch Protocol.** Measurement only (DROP-to-CHARGE time, bottom
temperature, charge temperature; Config → Statistics). It relies on
Config → Sampling → Keep ON, which returns Artisan to ON after OFF. Warm-up
automation comes from alarms relative to ON, not from BBP itself.

**Roaster state today.** No alarms defined. `autoDry=true` (DRY marks itself
at 300F), `autoDrop=false`. Buttons CHARGE/DRY/FCs/FCe send WebSocket events;
DROP and COOL END run Hottop commands; OFF sends the WebSocket OFF.

## Design

### Schedule, not prose

The recommender's output gains a structured `schedule`: a list of moves,
each `{trigger, action, value}` where `trigger` is `{"bt": F}` or
`{"after": "CHARGE"|"DRY"|"FCs", "seconds": N}` and `action` is
`heater`, `fan`, or `drop`. The model does not write it from scratch. The
deterministic baseline is the reference roast's actual control moves
(`roast_narrative.build_control_timeline`, already what the planner uses)
and the model may only express deltas against it (shift a cut by ±X F or
±N s, change a percentage by ±10, change dev seconds). `roast_plan`-style
validation rejects anything outside bounds before it is written: heater
0-100 and non-increasing after the first cut, fan 0-100, a BT ceiling under
`SAFETY_EJECT_BT`, dev time inside the bracket the cup has approved.

### Alarm set per bean

`roast_plan` (or a new `alarm_export.py`) turns a schedule into an `.alrm`:

1. Pre-FC heater staircase as BT-triggered Slider 4 rows (from the
   reference's BT at each cut, plus any delta), fan moves as Slider 1 rows.
   A parallel time-triggered set (From CHARGE) is exported too, so the
   time-vs-temp choice is which set is loaded.
2. Development: `From FC START, offset N - ear_latency, action DROP`, guarded
   by "If Alarm" on a `BT > 350` row so a false first crack cannot start the
   timer. `ear_latency` is measured per session from the sidecars
   (`declared_at_elapsed - elapsed`; 17-18 s on 2026-09-13).
3. Guards: `BT > 400 → DROP` unconditionally; a Pop Up at 356 as today.
4. Cooling: `From DROP + N s → COOL END` (the existing all-off Hottop
   command), and `→ OFF`.
5. Warm-up (only after the Hottop test below passes): `From ON`: heater 100;
   `BT > 370` → heater 0, fan up; `BT < 300` guarded by the previous row →
   SAY "charge", so the current manual pre-heat-to-370, cool-to-300 becomes
   three rows and Keep ON carries Artisan from one batch to the next.

The file is named by bean and rsynced to the roaster next to the ear; the
session sheet says which set to load.

### Ear pushes first crack (Phase D)

`ear_session` sends the addEvent push when the rule declares, behind a
`--push-artisan` flag (default off until it has been watched for a session).
The alert stays. The ear keeps recording the onset in the sidecar, so the
analyzer still sees the true onset and the mark's offset from it.

## Build order and gates

| Step | What | Gate | Size |
|---|---|---|---|
| 0 | A reference roast exists and is cupped | — | roasting, not code |
| 1 | Deterministic `.alrm` export from the reference roast (no LLM). One roast hands-off except CHARGE and the FCs click. | step 0 | ~1 day |
| 2 | Ear pushes FCs; timed DROP alarm with BT guard; latency subtraction | step 1 worked for a session | small |
| 3 | Structured schedule deltas in the recommender, validated, feeding step 1 | two clean sessions on step 1-2 | medium, most care |
| 4 | Keep ON + warm-up alarms | Hottop test passes | config + a test day |

## Open questions to settle on a roast day

- **Does the Hottop obey Artisan after OFF without a power cycle?** Enable
  Keep ON, press OFF after a roast, move the heater slider. If the machine
  responds, step 4 is possible and the restart dance ends. If it ignores
  commands until its ready screen, between-batch stays manual and only the
  roast is automated.
- **Exact WebSocket push names** on the roaster's Config → Ports → WebSocket
  tab.
- **Does a Slider alarm take effect while Artisan is in Hottop control
  mode** the same way a hand-moved slider does? Expected yes; confirm on the
  first alarm-driven cut.
- **Auto CHARGE**: whether Artisan's auto-charge detection is reliable with
  a 300F hot charge, which would remove the last button during a roast.

## Risks

- A wrong schedule is executed exactly. The validation bounds and the
  BT-ceiling alarm are the defence; the Hottop's own 408F eject and the
  1 s watchdog sit under them. Failure mode is dumped beans.
- A false first crack (drum click burst) starts the drop timer early; the
  BT-band guard and the 7-in-20 s rule are the defence.
- Alarm timing is only as good as the events: CHARGE must be marked, and a
  late FCs push shifts the drop by the same amount.
- Artisan settings live on the roaster; the `.alrm` in the repo is the
  versioned truth, and the loaded set must match it.
