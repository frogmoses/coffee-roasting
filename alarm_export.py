"""Alarm export: a roast schedule -> Artisan alarm rows -> `.alrm` / `[Alarms]`.

Artisan evaluates its alarm table on every sample while it is ON (recording
or not). A row is armed once its From event has happened (ON always; CHARGE,
DRY END, FC START ... once marked; START once recording) and its guards
allow it (If Alarm: row N has fired; But Not: row N has not). An armed row
fires when EITHER its time offset after the From event has elapsed OR its
temperature condition holds (source BT/ET compared `<` `>` to a value), so a
row carrying both is a BT trigger with a time fallback in one line. Each
row fires once per ON. Verified in `artisanlib/canvas.py` (`sample`,
`processAlarm`) at v4.0.0; encodings are `artisan_conf.ALARM_*`. Guard
indexes are stored 0-based (the dialog shows them 1-based).

Two things the encoding forces:

- Bean temperature falls from the 300F charge to the turning point before
  it rises, so a `BT > 262` row armed at CHARGE would fire at the charge.
  Every BT-triggered move is therefore guarded by a "past the turning
  point" marker row (`From CHARGE, BT < lowest trigger - 10`) that fires on
  the descent, below every trigger, and only then arms the staircase.
- The warm-up runs From ON with the drum empty, so a `BT > 370 -> heater 0`
  row must never fire during a roast. Every From-ON row is But-Not guarded
  by a "roast started" marker (`From CHARGE, BT > 0`), and the rows that
  fire immediately at ON (control on, motor on, heater 100) come before the
  roast can start anyway.
- Artisan's Hottop driver discards every command until control is active
  (`hottop.py setHottop` stores nothing unless `_control_active`), and the
  From-ON rows fire seconds after ON, long before an operator could press
  CONTROL. So the first warm-up row presses a custom button whose Hottop
  command is `control(1)`: `main.py` runs `HottopControlOn(autosuper=True)`
  from it, which turns on super-user mode, takes control, and starts the
  drum. Taking control is idempotent, so the row is safe on every ON. That
  call is scheduled with a one-shot timer, so the buttons that must run
  under control (motor, door, stirrer) sit in a later row.
- The descent marker (`BT < lowest trigger - 10`) also carries a clock
  twin at CHARGE + 45 s: a CHARGE marked after BT has already rebounded
  past the lowest trigger would otherwise never arm the staircase, and
  the heater would sit at 100% until the 398F ceiling.

The rows go out two ways: `.alrm` (JSON with plural keys, loaded via Config
-> Alarms -> Load, replaces the whole table) and the `[Alarms]` section of
`Artisan.conf` (singular keys, for the dev machine's Simulator runs; needs
at least two rows or Qt stores the table as a scalar).
"""

import json

from artisan_conf import ALARM_FROM, ALARM_ACTIONS
from roast_schedule import (
    trigger_kind, expected_seconds, fmt_mmss, DEFAULT_WARMUP, DEFAULT_GUARDS,
)

# From-event codes (alarmtime), see artisan_conf.ALARM_FROM
FROM_ON, FROM_CHARGE, FROM_FCS, FROM_DROP = 9, 0, 2, 6
# Condition codes (alarmcond)
COND_LT, COND_GT = 0, 1
# Source codes (alarmsource)
SRC_NONE, SRC_BT = -3, 1
# Action codes (alarmaction); slider 1 is the Hottop fan, slider 4 the heater
ACT_NONE, ACT_POPUP, ACT_BUTTON, ACT_FAN, ACT_HEATER = -1, 0, 2, 3, 6
ACT_DROP, ACT_COOL = 13, 14
# The roaster's custom buttons (artisan/Artisan.conf [ExtraEventButtons]),
# 1-based as the Event Button action expects them. A test pins these to the
# repo copy of the settings file.
CONTROL_BUTTON = 3       # "Control On": control(1) -> super-user + control + drum
MOTOR_BUTTON = 1         # "Motor On": motor(1)
DOOR_CLOSE_BUTTON = 5    # "Door Close": solenoid(0)
STIRRER_OFF_BUTTON = 8   # "Stirrer Off": stirrer(0)
BUTTON_NAMES = {CONTROL_BUTTON: "Control On", MOTOR_BUTTON: "Motor On",
                DOOR_CLOSE_BUTTON: "Door Close", STIRRER_OFF_BUTTON: "Stirrer Off"}
# The descent marker sits this far under the lowest BT trigger ...
DESCENT_MARGIN_F = 10
# ... and also fires by clock this long after CHARGE (BT is near 185F on
# the median curve by then), so a late CHARGE mark still arms the staircase.
DESCENT_FALLBACK_S = 45
# A pop-up when BT passes this with the roast still running: on this machine
# every first crack has been marked by 366F, so a roast at this BT with no
# FCs mark has missed it (the ear missed, or the operator did). The row
# cannot know whether FCs is marked; it nags every roast, once.
FC_NAG_BT = 372
# COOL END this long after DROP (0 disables the row). The DROP button opens
# the door and runs the cooling tray; COOL END turns everything off.
DEFAULT_COOL_S = 240
# More rows than this is a sign the schedule is wrong, not a limit Artisan has.
MAX_ROWS = 30

ALRM_KEYS = ["alarmflags", "alarmguards", "alarmnegguards", "alarmtimes", "alarmoffsets",
             "alarmconds", "alarmsources", "alarmtemperatures", "alarmactions",
             "alarmbeep", "alarmstrings"]
CONF_KEYS = ["alarmflag", "alarmguard", "alarmnegguard", "alarmtime", "alarmoffset",
             "alarmcond", "alarmsource", "alarmtemperature", "alarmaction",
             "alarmbeep", "alarmstrings"]
ROW_KEYS = ["flag", "guard", "negguard", "from", "offset", "cond", "source",
            "temperature", "action", "beep", "string"]


def _row(**kw):
    """One alarm row with Artisan's defaults; `label` is ours, for humans."""
    row = {"flag": 1, "guard": -1, "negguard": -1, "from": FROM_CHARGE, "offset": 0,
           "cond": COND_GT, "source": SRC_NONE, "temperature": 0, "action": ACT_NONE,
           "beep": 0, "string": "", "label": ""}
    row.update(kw)
    row["offset"] = int(round(row["offset"]))
    return row


def warmup_rows(charge, roast_marker, motor_button=MOTOR_BUTTON):
    """The From-ON warm-up: control on (drum starts), motor on with the door
    closed and the stirrer off, heater 100 and fan 0 to the preheat BT,
    heater off and fan up to cool, then a "charge now" pop-up at the charge
    BT with the fan back to 0 and the heater to 100.

    `roast_marker` is the 0-based index of the "roast started" row; every
    warm-up row is But-Not guarded by it. Returns the rows; the caller
    appends them and wires the If-Alarm guards by position.
    """
    w = dict(DEFAULT_WARMUP, **(charge.get("warmup") or {}))
    preheat, charge_bt = w["preheat_bt"], w["charge_bt"]
    state_buttons = ",".join(str(b) for b in (motor_button, DOOR_CLOSE_BUTTON, STIRRER_OFF_BUTTON))
    rows = [
        _row(**{"from": FROM_ON}, offset=1, action=ACT_BUTTON, string=str(CONTROL_BUTTON),
             negguard=roast_marker, label="warm-up: Hottop control on (super-user, drum)"),
        _row(**{"from": FROM_ON}, offset=3, action=ACT_BUTTON, string=state_buttons,
             negguard=roast_marker, label="warm-up: drum motor on, door closed, stirrer off"),
        _row(**{"from": FROM_ON}, offset=5, action=ACT_HEATER, string="100",
             negguard=roast_marker, label="warm-up: heater 100%"),
        _row(**{"from": FROM_ON}, offset=5, action=ACT_FAN, string="0",
             negguard=roast_marker, label="warm-up: fan 0%"),
        _row(**{"from": FROM_ON}, source=SRC_BT, cond=COND_GT, temperature=preheat,
             action=ACT_HEATER, string=str(w["cool_heater"]), beep=1, negguard=roast_marker,
             label=f"warm-up: {preheat}F reached, heater {w['cool_heater']}%"),
        _row(**{"from": FROM_ON}, source=SRC_BT, cond=COND_GT, temperature=preheat,
             action=ACT_FAN, string=str(w["cool_fan"]), negguard=roast_marker,
             label=f"warm-up: fan {w['cool_fan']}% to cool"),
        _row(**{"from": FROM_ON}, source=SRC_BT, cond=COND_LT, temperature=charge_bt,
             action=ACT_POPUP, string=f"CHARGE NOW ({charge_bt}F): heater 100, fan 0",
             beep=1, negguard=roast_marker, label="warm-up: charge pop-up"),
        _row(**{"from": FROM_ON}, source=SRC_BT, cond=COND_LT, temperature=charge_bt,
             action=ACT_FAN, string="0", negguard=roast_marker, label="warm-up: fan 0%"),
        _row(**{"from": FROM_ON}, source=SRC_BT, cond=COND_LT, temperature=charge_bt,
             action=ACT_HEATER, string="100", negguard=roast_marker, label="warm-up: heater 100%"),
    ]
    # Heater 100 / fan 0 at ON must not undo a heater-off / fan-up that fired
    # first (a hot drum at ON): But Not the "preheat reached" row. The three
    # "cooled to charge" rows fire only after the preheat row: If Alarm it.
    return rows, {"heater_on": 2, "fan_on": 3, "preheat": 4, "cooled": (6, 7, 8)}


def schedule_to_alarms(schedule, envelope=None, mode="paired", ear_latency_s=0.0,
                       manual_drop=False, warmup=True, cool_after_s=DEFAULT_COOL_S,
                       motor_button=MOTOR_BUTTON):
    """Turn a validated schedule into Artisan alarm rows.

    mode: "paired" = BT trigger with its expected time as the fallback (one
    row each); "bt" = BT only; "time" = time only (time moves are always
    time rows). `ear_latency_s` is subtracted from the FCs -> DROP offset
    when the ear will be pushing the mark. `manual_drop` leaves the DROP
    timer out (the operator drops by hand; the BT ceiling row stays).

    Returns {"rows", "warnings", "errors"}; rows carry 0-based guard indexes.
    """
    env = envelope or {}
    charge = schedule.get("charge") or {}
    guards = dict(DEFAULT_GUARDS, **(schedule.get("guards") or {}))
    rows, warnings, errors = [], [], []

    def add(row):
        rows.append(row)
        return len(rows) - 1

    # -- markers
    roast_marker = add(_row(source=SRC_BT, cond=COND_GT, temperature=0,
                            label="marker: roast started (CHARGE)"))

    # -- warm-up (From ON)
    if warmup:
        wrows, idx = warmup_rows(charge, roast_marker, motor_button)
        base = len(rows)
        for r in wrows:
            add(r)
        rows[base + idx["heater_on"]]["negguard"] = base + idx["preheat"]
        rows[base + idx["fan_on"]]["negguard"] = base + idx["preheat"]
        for i in idx["cooled"]:
            rows[base + i]["guard"] = base + idx["preheat"]

    # -- descent marker for the BT-triggered staircase
    moves = schedule.get("moves", [])
    bt_triggers = [m["trigger"]["bt"] for m in moves if trigger_kind(m["trigger"]) == "bt"]
    descent = None
    if bt_triggers and mode != "time":
        level = min(bt_triggers) - DESCENT_MARGIN_F
        tp_bt = env.get("tp_bt") or 0
        if tp_bt and level <= tp_bt:
            errors.append(f"lowest BT trigger {min(bt_triggers)}F leaves no room for the "
                          f"descent marker above TP ({tp_bt:.0f}F)")
        descent = add(_row(source=SRC_BT, cond=COND_LT, temperature=level,
                           offset=DESCENT_FALLBACK_S,
                           label=f"marker: past the turning point (BT < {level}F "
                                 f"or CHARGE + {fmt_mmss(DESCENT_FALLBACK_S)})"))

    # -- pre-FC moves
    for mv in moves:
        kind = trigger_kind(mv["trigger"])
        action = ACT_HEATER if mv["action"] == "heater" else ACT_FAN
        name = f"{mv['action']} {mv['value']}%"
        secs = expected_seconds(mv, env)
        if kind == "time" or mode == "time":
            if secs is None:
                errors.append(f"{name}: no expected time for a time row")
                continue
            add(_row(offset=max(1, secs), action=action, string=str(mv["value"]),
                     label=f"{name} at CHARGE + {fmt_mmss(secs)}" + (
                         f" (planned at BT {mv['trigger']['bt']}F)" if kind == "bt" else "")))
            continue
        offset = 0
        if mode == "paired":
            if secs is None:
                warnings.append(f"{name}: no expected time, BT trigger only")
            else:
                offset = max(1, secs)
        add(_row(source=SRC_BT, cond=COND_GT, temperature=mv["trigger"]["bt"], offset=offset,
                 guard=descent, action=action, string=str(mv["value"]),
                 label=f"{name} at BT {mv['trigger']['bt']}F" + (
                     f" or CHARGE + {fmt_mmss(offset)}" if offset else "")))

    # -- first crack helpers
    add(_row(source=SRC_BT, cond=COND_GT, temperature=guards["fc_popup_bt"], action=ACT_POPUP,
             string="FC imminent: listen, mark FCs at the first crack", beep=1,
             label=f"pop-up at {guards['fc_popup_bt']}F (FC imminent)"))
    fc_guard = add(_row(source=SRC_BT, cond=COND_GT, temperature=guards["fc_min_bt"],
                        label=f"marker: first crack possible (BT > {guards['fc_min_bt']}F)"))
    add(_row(source=SRC_BT, cond=COND_GT, temperature=FC_NAG_BT, action=ACT_POPUP, beep=1,
             string="No FCs mark yet? Mark it NOW (the drop timer runs from it)",
             label=f"pop-up at {FC_NAG_BT}F (no FCs mark yet? mark it now)"))

    # -- post-FC moves and the drop
    for mv in schedule.get("post_fc", []):
        action = ACT_HEATER if mv["action"] == "heater" else ACT_FAN
        after = max(1, int(mv["trigger"]["seconds"]))
        add(_row(**{"from": FROM_FCS}, offset=after, guard=fc_guard, action=action,
                 string=str(mv["value"]),
                 label=f"{mv['action']} {mv['value']}% at FCs + {fmt_mmss(after)}"))
    dev = int(schedule.get("drop", {}).get("seconds", 0))
    if not manual_drop and dev:
        offset = max(1, dev - int(round(ear_latency_s)))
        add(_row(**{"from": FROM_FCS}, offset=offset, guard=fc_guard, action=ACT_DROP, beep=1,
                 string=f"DROP at FCs + {fmt_mmss(dev)}",
                 label=f"DROP at FCs + {fmt_mmss(offset)}" + (
                     f" (= {fmt_mmss(dev)} dev minus {ear_latency_s:.0f}s ear latency)"
                     if ear_latency_s else "")))
    add(_row(source=SRC_BT, cond=COND_GT, temperature=guards["bt_ceiling"], action=ACT_DROP,
             beep=1, string=f"BT ceiling {guards['bt_ceiling']}F",
             label=f"DROP if BT > {guards['bt_ceiling']}F (safety ceiling)"))
    if cool_after_s:
        add(_row(**{"from": FROM_DROP}, offset=cool_after_s, action=ACT_COOL,
                 label=f"COOL END at DROP + {fmt_mmss(cool_after_s)}"))

    if len(rows) > MAX_ROWS:
        warnings.append(f"{len(rows)} alarm rows; more than {MAX_ROWS} is suspicious")
    for i, r in enumerate(rows):
        for key in ("guard", "negguard"):
            if r[key] != -1 and not (0 <= r[key] < len(rows) and r[key] != i):
                errors.append(f"row {i + 1}: bad {key} index {r[key]}")
    return {"rows": rows, "warnings": warnings, "errors": errors}


# ------------------------------------------------------------ serialisation

def rows_to_alrm(rows):
    """The `.alrm` JSON object (plural keys, parallel lists)."""
    out = {key: [] for key in ALRM_KEYS}
    for r in rows:
        for alrm_key, row_key in zip(ALRM_KEYS, ROW_KEYS):
            val = r[row_key]
            if row_key == "temperature":
                val = float(val)
            elif row_key != "string":
                val = int(val)
            out[alrm_key].append(val)
    return out


def write_alrm(path, rows):
    """Write rows as Artisan writes them (compact JSON, trailing newline)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows_to_alrm(rows), f, indent=None, separators=(",", ":"), ensure_ascii=False)
        f.write("\n")


def read_alrm(path):
    """Rows back from a `.alrm` (labels are not stored; they come back empty)."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    n = len(data.get("alarmflags", []))
    rows = []
    for i in range(n):
        kw = {row_key: data[alrm_key][i] for alrm_key, row_key in zip(ALRM_KEYS, ROW_KEYS)
              if alrm_key in data}
        rows.append(_row(**kw))
    return rows


def rows_to_conf(conf, rows):
    """Write rows into an ArtisanConf's `[Alarms]` section (singular keys).

    Artisan reads this at start; the dev machine's Simulator runs use it.
    Refuses fewer than two rows (Qt would store a one-row table as a scalar).
    """
    if len(rows) < 2:
        raise ValueError("an alarm table needs at least two rows (Qt one-element quirk)")
    for conf_key, row_key in zip(CONF_KEYS, ROW_KEYS):
        values = []
        for r in rows:
            val = r[row_key]
            if row_key == "temperature":
                val = f"{float(val):g}"
            values.append(str(val))
        conf.set_list("Alarms", conf_key, values)


# ------------------------------------------------------------ presentation

def _when(r):
    """Human trigger text: 'ON +0:01', 'CHARGE BT>262 or +4:28', 'FCs +2:12'."""
    src = ALARM_FROM.get(r["from"], str(r["from"]))
    src = {"FC START": "FCs", "COOL END": "COOL"}.get(src, src)
    bits = []
    if r["source"] == SRC_BT:
        bits.append(f"BT {'<' if r['cond'] == COND_LT else '>'} {float(r['temperature']):g}F")
    if r["offset"]:
        bits.append(f"+{fmt_mmss(r['offset'])}")
    return f"{src} " + " or ".join(bits) if bits else src


def _what(r):
    """Human action text: 'Heater 80%', 'Motor On (button 1)', 'DROP'."""
    a = r["action"]
    if a == ACT_HEATER:
        return f"Heater {r['string']}%"
    if a == ACT_FAN:
        return f"Fan {r['string']}%"
    if a == ACT_BUTTON:
        names = [BUTTON_NAMES.get(int(b.strip()), "?") for b in r["string"].split(",") if b.strip().isdigit()]
        return f"Event Button {r['string']}" + (f" ({', '.join(names)})" if names else "")
    if a == ACT_POPUP:
        return f"Pop Up '{r['string']}'"
    if a == ACT_NONE:
        # A marker row only records that something happened, for other
        # rows' if/unless conditions; say what it marks
        mark = (r.get("label") or "").split("marker:", 1)[-1].split("(")[0].strip()
        return f"(no action: marks '{mark}')" if mark else "(no action)"
    return ALARM_ACTIONS.get(a, str(a))


def describe_rows(rows):
    """One printable line per row, guards shown 1-based like the dialog."""
    lines = []
    for i, r in enumerate(rows):
        guards = []
        if r["guard"] != -1:
            guards.append(f"if #{r['guard'] + 1}")
        if r["negguard"] != -1:
            guards.append(f"unless #{r['negguard'] + 1}")
        tail = f"  [{', '.join(guards)}]" if guards else ""
        beep = " *" if r["beep"] else ""
        lines.append(f"{i + 1:>2}. {_when(r):<26} -> {_what(r)}{beep}{tail}")
    return lines
