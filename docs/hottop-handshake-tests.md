# Hottop Handshake Tests

Why the Hottop stops obeying Artisan between batches, and how to find the
sequence that works. No beans needed: every test runs with an empty drum.
Written 2026-09-13 from the Artisan 4.0.0 driver source
(`artisanlib/hottop.py`, `canvas.py`, `main.py`) and that day's roaster log.

## What the driver does (verified in source)

- Artisan drives the machine only while its **control** flag is on
  (super-user mode via right-click on the timer LCD, then the CONTROL
  button, or a `control(1)` Hottop command). Each machine frame is answered
  with a control frame carrying the last commanded heater / fan / motor /
  solenoid / stirrer. DROP and COOL END only change those values.
- Control can be released **only while BT is under 350F**, and a refused
  release is silent.
- **OFF disconnects the Hottop only if control is off.** With control on,
  the connection survives OFF, RESET and the next ON, still sending the
  COOL END all-zero frame three times a second.
- RESET never touches the Hottop. ON connects only when no connection
  exists; a surviving one is reused.
- Pressing CONTROL while control is on turns it off (once BT allows), which
  stops the communication loop but leaves the dead connection in place. ON
  then cannot reconnect and CONTROL "turns on" a loop that no longer runs.
  Only exiting Artisan clears it.
- Super-user mode is not saved, and the right-click toggles: on
  2026-09-13 the log shows seven ON/OFF cycles and three super toggles
  between batches #24 and #25, four cycles before #26, and one full exit.

Unknown from source: whether the firmware ignores control frames during its
own cooling countdown or until the ready screen. Test 0 measures that.

## Instruments

**Artisan device logging** is on in the repo settings (`[Device]
device_logging=true`, pushed to the roaster 2026-09-13). With it on the log
records every frame written and every reply received, at INFO:

```bash
tail -f ~/.local/share/artisan-scope/Artisan/artisan.log
```

It rotates at 800 KB with two backups, about 20 minutes of frames per
file, so copy the files right after a test. "Hottop connected",
"Hottop disconnected" and "Hottop control turned on/off" appear only in
Artisan's message bar, not the log: write them down.

**The probe** speaks the same protocol without Artisan, from the roaster
laptop (Artisan must be closed; they cannot share the port):

```bash
run_ear hottop_probe.py
```

It prints a decoded line whenever anything changes (BT, ET, heater, fans,
motor, solenoid, stirrer, chaff tray, and the bytes Artisan does not decode,
with a `CHANGED` note when one of those flips) and logs every frame both
ways to `~/CodeProjects/ear/captures/hottop_probe_<date>_<time>.log`.
Commands: `c` toggles control frames, `h 60` heater, `f 3` main fan (0-10),
`m 1` motor, `s 1` solenoid, `st 1` stirrer, `drop` and `coolend` replay the
two Artisan buttons, `e` goes back to echoing the machine, `?` status,
`q` quits with the heater at 0. Above `--max-bt-f` (default 300F) the heater
is forced to 0. Expect the machine to eject when control frames stop for a
second: that is the firmware's watchdog, and one of the things to observe.

Copy the evidence to the dev machine afterwards:

```bash
scp 'roaster:~/.local/share/artisan-scope/Artisan/artisan.log*' 'roaster:~/CodeProjects/ear/captures/hottop_probe_*.log' ear/captures/
```

## Test 0: map the firmware states (probe, no Artisan)

Empty drum, Artisan closed, Hottop plugged in and powered.

1. Ready screen: start the probe. Note whether frames arrive at all before
   any button is pressed, and the `unk` bytes.
2. `c` then `m 1`, `h 60`: does the echoed heater follow? Does the panel
   turn blue (USB mode)?
3. Let BT reach about 250F, then `drop`: eject door and cooling fan as with
   the DROP button. Note the panel (cooling countdown or not) and the
   `CHANGED` bytes.
4. During the countdown: `coolend`, then `h 40`, `m 1`. Does the echo
   follow, or is the heater ignored until the countdown ends?
5. Let the countdown finish (or whatever the panel does), then repeat
   step 4 at the ready screen.
6. `c` (control off) and watch what the machine does after one second of
   silence, and whether it needs a panel button before it answers `c` again.
7. Repeat step 2 to 4 once more while starting from a warm drum, since
   that is the between-batch case.

The answer to "does the firmware accept the heater during cooling / only at
the ready screen / only after a panel press" comes straight from the echoed
values; the `unk` bytes that change at eject and at the ready screen are
the state byte for the automation plan.

## Tests A to C: end-of-batch sequences (Artisan, empty drum)

For each: ON, right-click the timer LCD until the message bar says
"super on", CONTROL ("Hottop control turned on"), heater to 60% until BT is
near 250F, then the sequence. Note the message bar and the panel at every
step, then whether the heater slider moves the echoed heater on the next
batch. Keep Artisan open between sequences unless the sequence says exit.

| | Sequence | Prediction from the driver |
|---|---|---|
| A | DROP, COOL END, wait until BT < 350F, CONTROL ("control turned off"), OFF, then ON and CONTROL | clean reconnect, no dance |
| B | DROP, COOL END, OFF at once, ON, do not touch super or CONTROL, move the heater slider | works unless the firmware refuses, since control never dropped |
| C | today's routine: DROP, COOL END, OFF, RESET, ON, right-click, CONTROL | the dance; the log shows which press killed the loop |

In the log, "write(" lines are Artisan sending and "valid message received"
lines are the machine answering. Three outcomes are distinguishable:

- writes stop: Artisan-side (control released and loop stopped, dead
  connection);
- writes continue, replies stop: the machine went quiet (USB re-enumerated,
  or the firmware left USB mode);
- writes and replies continue but the echoed heater ignores the command:
  the firmware's ready-screen rule.

## What each result means

- B passes: the between-batch routine is "never release control", and the
  Keep ON warm-up in `automation-plan.md` is possible.
- Only A passes: add a Hottop command button that releases control once BT
  allows, and replace the restart dance with that wait.
- Test 0 shows the heater is ignored until the ready screen: between-batch
  stays manual and only the roast itself gets automated.

## Results (2026-09-14, empty drum, Brian)

**Test 0 (probe):** frames arrive at the ready screen before any panel
press; the echoed heater follows `h 60`; after `drop` there is **no cooling
countdown**, the blue USB screen just stays; `coolend` then `h 40`, `m 1`
echo at once, ready screen or not; the same from a warm drum. `c` (control
off) **turns the Hottop off** and it has to be restarted at the panel. With
control left on everything keeps working.

**Tests A to C (Artisan):** every control works back to back through DROP,
COOL END, OFF and ON, with no countdown and no wait. Pressing CONTROL again
turns the Hottop off, which is what test A's release step and the old
routine's CONTROL press were doing: the restart dance was self-inflicted.
COOL END stops the drum motor (its Hottop command ends in `motor(0)`), so
the motor has to be restarted before the next warm-up.

**Decision (B passes):** never release control after the first CONTROL
press of the day. Between batches: DROP, COOL END, OFF, save the profile,
ON. No RESET, no CONTROL, no exit. The motor comes back from a From-ON
alarm row that presses custom button 1 (Motor On, `motor(1)`); Artisan's
`HottopControlOn` also turns the drum on, but only at the CONTROL press.
Keep ON is not needed (From-ON alarm rows fire on a manual ON too) and
would reset an unsaved roast, since the roaster has `autosaveflag=0`.
