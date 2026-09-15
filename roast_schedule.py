"""Roast schedule: the designed plan for one roast on this Hottop.

A schedule is what the profile designer writes, the validator bounds, the
alarm exporter turns into Artisan rows, and the session sheet prints. It is
deliberately small because the machine only has a few levers with real
resolution: the charge state, a heater staircase of 20-30% moves (a single
10% step is invisible in BT rate-of-rise for two minutes; ET lags 60-70 s),
the fan, and the seconds from first crack to DROP.

Shape (see the plan file for the reasoning):

    {"schedule_version": 1,
     "charge": {"bt": 300, "heater": 100, "fan": 0,
                "warmup": {"preheat_bt": 370, "cool_heater": 0,
                           "cool_fan": 100, "charge_bt": 300}},
     "moves": [{"id": "fan10", "trigger": {"after": "CHARGE", "seconds": 15},
                "expect": {"bt": 252}, "action": "fan", "value": 10, "why": "..."},
               {"id": "h90", "trigger": {"bt": 262},
                "expect": {"after": "CHARGE", "seconds": 268},
                "action": "heater", "value": 90, "why": "..."}],
     "post_fc": [{"trigger": {"after": "FCs", "seconds": 0},
                  "action": "fan", "value": 40, "why": "..."}],
     "drop": {"after": "FCs", "seconds": 150},
     "guards": {"bt_ceiling": 398, "fc_min_bt": 350, "fc_popup_bt": 356},
     "predicted": {"fc_time_s": 640, "fc_bt": 361, "drop_bt": 385,
                   "confidence": "measured"}}

Triggers come in two forms. `{"bt": F}` fires when the bean temperature
rises through F; `{"after": EVENT, "seconds": N}` fires N seconds after
CHARGE, DRY, or FCs. Every move carries an `expect` twin in the other unit
(filled from the machine envelope's median curve when the designer leaves
it out) so the exporter can write both a BT row and a time row for it.
Bean temperature falls through the turning point (TP) for the first ~90 s
after a hot charge, so a BT value occurs twice there: the early fan moves
are time-triggered, the heater staircase is BT-triggered, and the exporter
guards every BT row behind a "past the turning point" marker. DROP is always timed
from FCs; drop temperature is a projection, never a trigger.

The validator encodes what the roaster has measured about this machine and
what the operator will drink (no dark roast: development capped at 180 s).
It clamps what it safely can (the 10% control grid), records the clamps,
and refuses the rest. With a `baseline` (a revision of an earlier version)
it also limits how much may change at once, so a cup can be attributed to
one deliberate change.
"""

import copy
from statistics import median

from roast_metrics import SAFETY_EJECT_BT
from roast_plan import EJECT_MARGIN_F, DEFAULT_POST_FC_RISE_F_PER_MIN

SCHEDULE_VERSION = 1

# Spacing between heater moves: a step shows in ET only after ~60-70 s, so
# two cuts closer than MIN_HEATER_GAP_S are one ramp the machine cannot tell
# apart from a bigger single cut (warning; the operator's own staircase runs
# 52-68 s). Under SIMULTANEOUS_S they are one move written twice (error).
MIN_HEATER_GAP_S = 60
SIMULTANEOUS_S = 20
# A heater step larger than this is not a trim; refuse it.
MAX_HEATER_STEP = 30
# Development window the operator will drink (he stops long batches at
# ~3:00 himself) and the floor under which a batch is undrinkably sour.
DEV_MIN_S = 60
DEV_CAP_S = 180
# Fan above this stalls the rise on this machine; last heater under this
# before FC bakes the finish.
MAX_FAN = 60
MIN_LAST_HEATER = 40
# A time trigger this long after TP earns a warning (BT tracks the bean better).
TP_GUARD_S = 30
# A BT trigger must sit this far above the turning point: the alarm exporter
# guards BT rows with a "past TP" marker that fires on the descent below the
# lowest trigger, and that marker needs room above the TP minimum.
TP_BT_MARGIN_F = 20
# Bound on the number of pre-FC moves: more than this is noise on a Hottop.
MAX_PRE_FC_MOVES = 8
# Projected drop must stay under this (408F eject minus the planner margin).
BT_CEILING_DEFAULT = SAFETY_EJECT_BT - EJECT_MARGIN_F
# First crack is impossible under this BT; the DROP timer must not start
# on a false crack below it. The Hottop panel flags FC imminent at 356F.
FC_MIN_BT = 350
FC_POPUP_BT = 356
# Revision bounds: how far one version may move from its baseline.
REVISION_MAX_CHANGES = 3
REVISION_BOUNDS = {"bt": 20, "seconds": 30, "value": 20, "dev": 30}
# Predicted FC outside the measured window by more than this is suspect.
FC_PREDICTION_SLACK_S = 90

DEFAULT_WARMUP = {"preheat_bt": 370, "cool_heater": 0, "cool_fan": 100, "charge_bt": 300}
DEFAULT_GUARDS = {"bt_ceiling": BT_CEILING_DEFAULT, "fc_min_bt": FC_MIN_BT,
                  "fc_popup_bt": FC_POPUP_BT}


def fmt_mmss(seconds):
    """Seconds -> M:SS (None -> '--:--')."""
    if seconds is None:
        return "--:--"
    s = max(0, int(round(seconds)))
    return f"{s // 60}:{s % 60:02d}"


def new_schedule(charge=None, moves=None, post_fc=None, drop_s=150,
                 guards=None, predicted=None):
    """A schedule dict with every section present (defaults filled in)."""
    charge = dict(charge or {})
    charge.setdefault("bt", 300)
    charge.setdefault("heater", 100)
    charge.setdefault("fan", 0)
    charge.setdefault("warmup", dict(DEFAULT_WARMUP))
    return {
        "schedule_version": SCHEDULE_VERSION,
        "charge": charge,
        "moves": list(moves or []),
        "post_fc": list(post_fc or []),
        "drop": {"after": "FCs", "seconds": int(drop_s)},
        "guards": dict(DEFAULT_GUARDS, **(guards or {})),
        "predicted": dict(predicted or {}),
    }


# ------------------------------------------------------------ curve helpers
# An envelope's `median_curve` is a list of {"time": s, "bt": F} rows from
# CHARGE, monotonic in time. These helpers translate between the two trigger
# units on that curve.

def tp_index(curve):
    """Index of the turning point (minimum BT) on a curve, or 0."""
    if not curve:
        return 0
    return min(range(len(curve)), key=lambda i: curve[i]["bt"])


def time_at_bt(curve, bt):
    """Seconds after CHARGE at which BT first rises through `bt` past TP.

    Linear interpolation between rows; None if the curve never gets there.
    """
    if not curve:
        return None
    start = tp_index(curve)
    for i in range(start + 1, len(curve)):
        a, b = curve[i - 1], curve[i]
        if b["bt"] >= bt:
            if b["bt"] == a["bt"]:
                return float(b["time"])
            frac = (bt - a["bt"]) / (b["bt"] - a["bt"])
            frac = max(0.0, min(1.0, frac))
            return a["time"] + frac * (b["time"] - a["time"])
    return None


def bt_at_time(curve, seconds):
    """BT on the curve at `seconds` after CHARGE (interpolated), or None."""
    if not curve or seconds is None:
        return None
    if seconds <= curve[0]["time"]:
        return float(curve[0]["bt"])
    for i in range(1, len(curve)):
        a, b = curve[i - 1], curve[i]
        if b["time"] >= seconds:
            if b["time"] == a["time"]:
                return float(b["bt"])
            frac = (seconds - a["time"]) / (b["time"] - a["time"])
            return a["bt"] + frac * (b["bt"] - a["bt"])
    return None


# ------------------------------------------------------------ move helpers

def trigger_kind(trigger):
    """'bt' or 'time' (or None for a malformed trigger)."""
    if not isinstance(trigger, dict):
        return None
    if "bt" in trigger:
        return "bt"
    if "after" in trigger and "seconds" in trigger:
        return "time"
    return None


def _event_offset(event, envelope):
    """Seconds from CHARGE to a From event, from the envelope's medians."""
    env = envelope or {}
    if event == "CHARGE":
        return 0.0
    if event == "DRY":
        return env.get("dry_time") or None
    if event == "FCs":
        stats = env.get("fc_stats") or {}
        return stats.get("fc_time_mean") or env.get("fc_time") or None
    return None


def expected_seconds(move, envelope=None):
    """Seconds after CHARGE at which a move is expected to fire.

    A time trigger answers directly (relative to its From event on the
    envelope); a BT trigger uses its `expect` twin, else the envelope curve.
    """
    trig = move.get("trigger", {})
    kind = trigger_kind(trig)
    if kind == "time":
        base = _event_offset(trig["after"], envelope)
        return None if base is None else base + float(trig["seconds"])
    expect = move.get("expect") or {}
    if "seconds" in expect:
        base = _event_offset(expect.get("after", "CHARGE"), envelope)
        return None if base is None else base + float(expect["seconds"])
    if kind == "bt":
        return time_at_bt((envelope or {}).get("median_curve") or [], trig["bt"])
    return None


def expected_bt(move, envelope=None):
    """BT at which a move is expected to fire (trigger, expect, or curve)."""
    trig = move.get("trigger", {})
    if trigger_kind(trig) == "bt":
        return float(trig["bt"])
    expect = move.get("expect") or {}
    if "bt" in expect:
        return float(expect["bt"])
    secs = expected_seconds(move, envelope)
    return bt_at_time((envelope or {}).get("median_curve") or [], secs)


def fill_expectations(schedule, envelope):
    """Fill each move's missing `expect` twin from the envelope curve, in place.

    Returns the list of move ids (or indexes) whose expectation could not be
    derived, so the caller can warn.
    """
    unresolved = []
    for i, mv in enumerate(schedule.get("moves", [])):
        kind = trigger_kind(mv.get("trigger"))
        expect = dict(mv.get("expect") or {})
        if kind == "bt" and "seconds" not in expect:
            secs = time_at_bt(envelope.get("median_curve") or [], mv["trigger"]["bt"])
            if secs is None:
                unresolved.append(mv.get("id") or i)
            else:
                expect = {"after": "CHARGE", "seconds": round(secs)}
        elif kind == "time" and "bt" not in expect:
            bt = expected_bt(mv, envelope)
            if bt is None:
                unresolved.append(mv.get("id") or i)
            else:
                expect = {"bt": round(bt)}
        mv["expect"] = expect
    return unresolved


def project_drop_bt(schedule, envelope=None):
    """Projected drop BT: FC BT plus the bean's post-FC rise over dev seconds."""
    stats = (envelope or {}).get("fc_stats") or {}
    fc_bt = stats.get("fc_bt_mean") or (schedule.get("predicted") or {}).get("fc_bt") or 360
    rise = stats.get("post_fc_rise") or DEFAULT_POST_FC_RISE_F_PER_MIN
    dev = float(schedule.get("drop", {}).get("seconds", 0))
    return fc_bt + rise * dev / 60.0


def _snap10(value):
    """Clamp to 0-100 and snap to the 10% grid the Hottop actually has."""
    v = max(0, min(100, int(round(float(value) / 10.0)) * 10))
    return v


# ------------------------------------------------------------ construction

def schedule_from_timeline(timeline, fc_time, envelope=None, dev_s=None):
    """Deterministic schedule from a roast's actual control moves.

    This is the no-LLM path: the moves of a logged roast become the plan
    (fan moves and anything near the turning point as time triggers, the
    heater cuts as BT triggers),
    post-FC moves are re-based on FCs, and the drop is `dev_s` seconds after
    FC (default: the roast's own development time is unknown here, so 150).
    `charge` comes from the timeline's start heater/fan. `expect` twins are
    filled from the timeline itself (the roast's own BT and time).
    """
    moves_in = timeline.get("moves", [])
    start_heater = timeline.get("start_heater")
    start_fan = timeline.get("start_fan")
    tp_time = (envelope or {}).get("tp_time") or 90.0
    tp_bt = (envelope or {}).get("tp_bt") or 0

    charge = {"heater": _snap10(start_heater if start_heater is not None else 100),
              "fan": _snap10(start_fan if start_fan is not None else 0)}
    moves, post_fc = [], []
    for mv in moves_in:
        control = (mv.get("control") or "").lower()
        if control not in ("heater", "fan"):
            continue
        value = mv.get("percentage")
        if value is None:
            continue
        rel = float(mv.get("rel_time", 0))
        # The 0:00 [CHARGE] row is the charge state, not a move.
        if rel <= 1.0 and mv.get("marker") == "CHARGE":
            charge[control] = _snap10(value)
            continue
        entry = {"id": f"{control[0]}{int(_snap10(value))}_{int(rel)}",
                 "action": control, "value": _snap10(value), "why": "from the logged roast"}
        if fc_time and rel > fc_time:
            entry["trigger"] = {"after": "FCs", "seconds": round(rel - fc_time)}
            post_fc.append(entry)
            continue
        bt = mv.get("bt")
        # Fan moves are airflow steps on the clock, not bean-state cuts, and
        # the early ones land on the descent; heater cuts track the bean once
        # BT is clear of the turning point.
        too_early = rel < tp_time + TP_GUARD_S or (tp_bt and bt is not None
                                                   and bt < tp_bt + TP_BT_MARGIN_F + 10)
        if control == "fan" or bt is None or too_early:
            entry["trigger"] = {"after": "CHARGE", "seconds": round(rel)}
            if bt is not None:
                entry["expect"] = {"bt": round(bt)}
        else:
            entry["trigger"] = {"bt": round(bt)}
            entry["expect"] = {"after": "CHARGE", "seconds": round(rel)}
        moves.append(entry)
    sched = new_schedule(charge=charge, moves=moves, post_fc=post_fc,
                         drop_s=dev_s if dev_s else 150)
    stats = (envelope or {}).get("fc_stats") or {}
    if stats.get("fc_time_mean"):
        sched["predicted"] = {
            "fc_time_s": round(stats["fc_time_mean"]),
            "fc_bt": round(stats.get("fc_bt_mean") or 0),
            "drop_bt": round(project_drop_bt(sched, envelope)),
            "confidence": "borrowed" if (envelope or {}).get("borrowed_from") else "measured",
        }
    return sched


def schedule_from_envelope(envelope, dev_s=150):
    """A schedule from the envelope's measured staircase (median time and BT
    per level). The baseline for a first design when no single roast is
    the anchor, and the fallback when the designer's output is unusable."""
    stair = (envelope or {}).get("staircase") or {}
    start = stair.get("start") or {}
    tp_time = (envelope or {}).get("tp_time") or 90.0
    tp_bt = (envelope or {}).get("tp_bt") or 0
    moves = []
    for row in stair.get("moves", []):
        control, value = row["control"], _snap10(row["value"])
        rel, bt = float(row["median_time"]), row.get("median_bt")
        entry = {"id": f"{control[0]}{value}", "action": control, "value": value,
                 "why": "the measured staircase"}
        too_early = rel < tp_time + TP_GUARD_S or (tp_bt and bt is not None
                                                   and bt < tp_bt + TP_BT_MARGIN_F + 10)
        if control == "fan" or bt is None or too_early:
            entry["trigger"] = {"after": "CHARGE", "seconds": round(rel)}
            if bt is not None:
                entry["expect"] = {"bt": round(bt)}
        else:
            entry["trigger"] = {"bt": round(bt)}
            entry["expect"] = {"after": "CHARGE", "seconds": round(rel)}
        moves.append(entry)
    charge = {"heater": _snap10(start.get("heater") if start.get("heater") is not None else 100),
              "fan": _snap10(start.get("fan") if start.get("fan") is not None else 0)}
    sched = new_schedule(charge=charge, moves=moves, drop_s=dev_s)
    stats = (envelope or {}).get("fc_stats") or {}
    if stats.get("fc_time_mean"):
        sched["predicted"] = {
            "fc_time_s": round(stats["fc_time_mean"]),
            "fc_bt": round(stats.get("fc_bt_mean") or 0),
            "drop_bt": round(project_drop_bt(sched, envelope)),
            "confidence": "borrowed" if (envelope or {}).get("borrowed_from") else "measured",
        }
    return sched


# The rules, in words, for the designer's prompt (kept next to the numbers).
RULES_TEXT = f"""\
Schedule rules (the validator refuses anything outside them):
- heater and fan values are on a 10% grid, 0-100; the charge state is
  heater 100 / fan 0 unless a reason is given;
- the heater never rises after CHARGE (before or after first crack); a
  single cut is at most {MAX_HEATER_STEP}%; a lone 10% step is allowed but
  is a trim the machine barely registers; heater moves closer than
  {MIN_HEATER_GAP_S} s apart act as one ramp; the heater into first crack
  stays at {MIN_LAST_HEATER}% or more;
- the fan never exceeds {MAX_FAN}%;
- early fan moves are clock-triggered from CHARGE; heater cuts are
  BT-triggered, at least {TP_BT_MARGIN_F}F above the turning point and at
  least 5F under the expected first-crack BT; triggers are in time order;
- at most {MAX_PRE_FC_MOVES} moves before first crack; post-FC moves are
  timed from FCs;
- DROP is timed from FCs, {DEV_MIN_S}-{DEV_CAP_S} s; the projected drop BT
  (FC BT + post-FC rise x dev minutes) stays under {BT_CEILING_DEFAULT}F;
- a revision changes at most {REVISION_MAX_CHANGES} things from its
  baseline, each within +/-{REVISION_BOUNDS["bt"]}F on a cut BT,
  +/-{REVISION_BOUNDS["seconds"]} s on a clock trigger,
  +/-{REVISION_BOUNDS["value"]}% on a value, +/-{REVISION_BOUNDS["dev"]} s
  on development, so a cup can be attributed to one change."""


# ------------------------------------------------------------ validation

def diff_schedules(new, base):
    """List the changes between two schedules for the revision bounds.

    Each change is {"what", "kind", "from", "to"}; kinds are "value", "bt",
    "seconds", "trigger_kind", "added", "removed", "dev", "charge",
    "post_fc". Heater moves pair up by level (the 80% cut with the 80% cut,
    wherever it sits), so adding an early 90% step is one change, not a
    shift of every pair; fan moves pair up in order.
    """
    changes = []
    for key in ("heater", "fan"):
        a, b = new.get("charge", {}).get(key), base.get("charge", {}).get(key)
        if a != b:
            changes.append({"what": f"charge {key}", "kind": "charge", "from": b, "to": a})
    pairs = []
    n_heat = [m for m in new.get("moves", []) if m.get("action") == "heater"]
    b_heat = [m for m in base.get("moves", []) if m.get("action") == "heater"]
    b_by_value = {m.get("value"): m for m in b_heat}
    for nm in n_heat:
        bm = b_by_value.pop(nm.get("value"), None)
        label = f"heater {nm.get('value')}%"
        if bm is None:
            changes.append({"what": label, "kind": "added", "from": None, "to": nm.get("value")})
        else:
            pairs.append((label, nm, bm))
    for value, bm in b_by_value.items():
        changes.append({"what": f"heater {value}%", "kind": "removed", "from": value, "to": None})
    n_fan = [m for m in new.get("moves", []) if m.get("action") == "fan"]
    b_fan = [m for m in base.get("moves", []) if m.get("action") == "fan"]
    for i in range(max(len(n_fan), len(b_fan))):
        label = f"fan move {i + 1}"
        if i >= len(b_fan):
            changes.append({"what": label, "kind": "added", "from": None, "to": n_fan[i].get("value")})
        elif i >= len(n_fan):
            changes.append({"what": label, "kind": "removed", "from": b_fan[i].get("value"), "to": None})
        else:
            pairs.append((label, n_fan[i], b_fan[i]))
    for label, nm, bm in pairs:
        if nm.get("value") != bm.get("value"):
            changes.append({"what": f"{label} value", "kind": "value",
                            "from": bm.get("value"), "to": nm.get("value")})
        nk, bk = trigger_kind(nm.get("trigger")), trigger_kind(bm.get("trigger"))
        if nk != bk:
            changes.append({"what": f"{label} trigger", "kind": "trigger_kind",
                            "from": bk, "to": nk})
        elif nk == "bt" and nm["trigger"]["bt"] != bm["trigger"]["bt"]:
            changes.append({"what": f"{label} BT", "kind": "bt",
                            "from": bm["trigger"]["bt"], "to": nm["trigger"]["bt"]})
        elif nk == "time" and (nm["trigger"]["seconds"] != bm["trigger"]["seconds"]
                               or nm["trigger"]["after"] != bm["trigger"]["after"]):
            changes.append({"what": f"{label} time", "kind": "seconds",
                            "from": bm["trigger"]["seconds"], "to": nm["trigger"]["seconds"]})
    n_post = [(m.get("action"), m.get("value"), (m.get("trigger") or {}).get("seconds"))
              for m in new.get("post_fc", [])]
    b_post = [(m.get("action"), m.get("value"), (m.get("trigger") or {}).get("seconds"))
              for m in base.get("post_fc", [])]
    if n_post != b_post:
        changes.append({"what": "post-FC moves", "kind": "post_fc", "from": b_post, "to": n_post})
    nd, bd = new.get("drop", {}).get("seconds"), base.get("drop", {}).get("seconds")
    if nd != bd:
        changes.append({"what": "dev seconds", "kind": "dev", "from": bd, "to": nd})
    return changes


def validate_schedule(schedule, envelope=None, prefs=None, baseline=None,
                      max_changes=REVISION_MAX_CHANGES):
    """Bound a schedule against the machine and the operator.

    Returns {"ok", "errors", "warnings", "clamped", "schedule"} where
    `schedule` is a deep copy with the clamps applied and `expect` twins
    filled. Errors make `ok` False; warnings do not.
    """
    prefs = prefs or {}
    env = envelope or {}
    sched = copy.deepcopy(schedule)
    errors, warnings, clamped = [], [], []

    # -- sections and defaults
    sched.setdefault("schedule_version", SCHEDULE_VERSION)
    charge = sched.setdefault("charge", {})
    charge.setdefault("bt", 300)
    charge.setdefault("warmup", dict(DEFAULT_WARMUP))
    guards = dict(DEFAULT_GUARDS, **(sched.get("guards") or {}))
    ceiling_max = SAFETY_EJECT_BT - EJECT_MARGIN_F
    if guards["bt_ceiling"] > ceiling_max:
        clamped.append(f"bt_ceiling {guards['bt_ceiling']} -> {ceiling_max}")
        guards["bt_ceiling"] = ceiling_max
    sched["guards"] = guards
    drop = sched.setdefault("drop", {"after": "FCs", "seconds": 150})
    if drop.get("after") != "FCs":
        errors.append("drop must be timed from FCs (drop temperature is an outcome)")

    # -- charge state
    for key, default in (("heater", 100), ("fan", 0)):
        raw = charge.get(key, default)
        snapped = _snap10(raw)
        if snapped != raw:
            clamped.append(f"charge {key} {raw} -> {snapped}")
        charge[key] = snapped
    if (charge["heater"] != 100 or charge["fan"] != 0) and not charge.get("why"):
        warnings.append("charge state is not heater 100 / fan 0 and gives no reason")

    # -- moves: shape, grid, expectations
    moves = sched.setdefault("moves", [])
    post_fc = sched.setdefault("post_fc", [])
    for group, label in ((moves, "move"), (post_fc, "post-FC move")):
        for i, mv in enumerate(group):
            name = mv.get("id") or f"{label} {i + 1}"
            if mv.get("action") not in ("heater", "fan"):
                errors.append(f"{name}: action must be heater or fan")
                continue
            raw = mv.get("value")
            if raw is None:
                errors.append(f"{name}: no value")
                continue
            snapped = _snap10(raw)
            if snapped != raw:
                clamped.append(f"{name} value {raw} -> {snapped}")
            mv["value"] = snapped
            kind = trigger_kind(mv.get("trigger"))
            if kind is None:
                errors.append(f"{name}: trigger must be {{bt}} or {{after, seconds}}")
            elif group is post_fc and (kind != "time" or mv["trigger"].get("after") != "FCs"):
                errors.append(f"{name}: post-FC moves are timed from FCs")
    if errors:
        return {"ok": False, "errors": errors, "warnings": warnings,
                "clamped": clamped, "schedule": sched}

    if len(moves) > MAX_PRE_FC_MOVES:
        errors.append(f"{len(moves)} pre-FC moves; at most {MAX_PRE_FC_MOVES}")

    curve = env.get("median_curve") or []
    if curve:
        unresolved = fill_expectations(sched, env)
        for name in unresolved:
            warnings.append(f"{name}: expectation not on the envelope curve")
    else:
        warnings.append("no machine envelope curve: timing checks skipped")

    # -- ordering, TP rule, heater spacing
    tp_time = env.get("tp_time") or 90.0
    times = [expected_seconds(mv, env) for mv in moves]
    prev = None
    for mv, t in zip(moves, times):
        name = mv.get("id") or f"{mv['action']} {mv['value']}"
        kind = trigger_kind(mv["trigger"])
        if t is None:
            continue
        if prev is not None and t < prev - 5:
            errors.append(f"{name}: expected at {fmt_mmss(t)}, before the previous move")
        prev = t
        if t >= tp_time + TP_GUARD_S and kind == "time":
            warnings.append(f"{name}: time trigger after TP; a BT trigger tracks the bean")

    stats = env.get("fc_stats") or {}
    fc_bt_mean = stats.get("fc_bt_mean") or 0
    for mv in moves:
        if trigger_kind(mv["trigger"]) == "bt":
            name = mv.get("id") or f"{mv['action']} {mv['value']}"
            bt = mv["trigger"]["bt"]
            if fc_bt_mean and bt >= fc_bt_mean - 5:
                errors.append(f"{name}: BT trigger {bt}F is at or past first crack (~{fc_bt_mean:.0f}F)")
            tp_bt = env.get("tp_bt") or 0
            if tp_bt and bt < tp_bt + TP_BT_MARGIN_F:
                errors.append(f"{name}: BT trigger {bt}F is too close to the turning point "
                              f"({tp_bt:.0f}F); the descent guard needs {TP_BT_MARGIN_F}F of room")

    heater_level = charge["heater"]
    last_heater_t = None
    for mv, t in zip(moves, times):
        name = mv.get("id") or f"{mv['action']} {mv['value']}"
        if mv["action"] == "fan":
            if mv["value"] > MAX_FAN:
                errors.append(f"{name}: fan {mv['value']}% stalls the rise (max {MAX_FAN})")
            continue
        step = heater_level - mv["value"]
        if step < 0:
            errors.append(f"{name}: heater rises {heater_level} -> {mv['value']}; never after CHARGE")
        elif step > MAX_HEATER_STEP:
            errors.append(f"{name}: heater step {step}% is more than {MAX_HEATER_STEP}")
        elif step == 10:
            warnings.append(f"{name}: a lone 10% step is invisible in BT rate-of-rise for ~2 min")
        if last_heater_t is not None and t is not None:
            gap = t - last_heater_t
            if gap < SIMULTANEOUS_S:
                errors.append(f"{name}: {gap:.0f}s after the previous heater move; that is one move, not two")
            elif gap < MIN_HEATER_GAP_S:
                warnings.append(f"{name}: {gap:.0f}s after the previous heater move; under "
                                f"{MIN_HEATER_GAP_S}s the machine sees a ramp, not two cuts")
        if t is not None:
            last_heater_t = t
        heater_level = mv["value"]
    if moves and heater_level < MIN_LAST_HEATER:
        errors.append(f"heater into FC is {heater_level}%; keep at least {MIN_LAST_HEATER}")

    # -- post-FC moves
    post_level = heater_level
    for mv in post_fc:
        name = mv.get("id") or f"post-FC {mv['action']} {mv['value']}"
        if mv["action"] == "heater":
            if mv["value"] > post_level:
                errors.append(f"{name}: heater rises after FC")
            post_level = mv["value"]
        elif mv["value"] > MAX_FAN:
            errors.append(f"{name}: fan {mv['value']}% after FC (max {MAX_FAN})")

    # -- development and drop projection
    dev = float(drop.get("seconds", 0))
    dev_cap = prefs.get("dev_cap_s", DEV_CAP_S)
    if dev < DEV_MIN_S or dev > dev_cap:
        errors.append(f"dev {dev:.0f}s outside {DEV_MIN_S}-{dev_cap}s")
    projected = project_drop_bt(sched, env)
    if projected >= guards["bt_ceiling"]:
        errors.append(f"projected drop {projected:.0f}F reaches the {guards['bt_ceiling']}F ceiling")
    if post_fc:
        warnings.append("post-FC moves change the rise the drop projection was measured on")

    # -- prediction sanity
    pred = sched.get("predicted") or {}
    if pred.get("fc_time_s") and stats.get("count"):
        lo = stats["fc_time_min"] - FC_PREDICTION_SLACK_S
        hi = stats["fc_time_max"] + FC_PREDICTION_SLACK_S
        if not (lo <= pred["fc_time_s"] <= hi):
            warnings.append(f"predicted FC {fmt_mmss(pred['fc_time_s'])} is outside the measured "
                            f"{fmt_mmss(stats['fc_time_min'])}-{fmt_mmss(stats['fc_time_max'])} window")

    # -- revision bounds
    if baseline:
        changes = diff_schedules(sched, baseline)
        if len(changes) > max_changes:
            errors.append(f"{len(changes)} changes from the baseline; at most {max_changes}")
        for ch in changes:
            bound = REVISION_BOUNDS.get(ch["kind"])
            if bound is None or ch["from"] is None or ch["to"] is None:
                continue
            if abs(float(ch["to"]) - float(ch["from"])) > bound:
                errors.append(f"{ch['what']} {ch['from']} -> {ch['to']} exceeds the +/-{bound} bound")
        sched["changes_from_baseline"] = changes

    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "clamped": clamped, "schedule": sched}


# ------------------------------------------------------------ execution check

def compare_execution(schedule, timeline, metrics, envelope=None):
    """Did the roast do what the schedule said?

    Pairs each designed move with the logged move of the same control and
    value nearest its expected time, and reports the time and BT deltas;
    unmatched designed moves are `missed`, unmatched logged moves are
    `extra`. Also compares development seconds and the FC/drop predictions
    with what happened.
    """
    fc_time = float(metrics.get("fc_time") or 0)
    logged = []
    for mv in timeline.get("moves", []):
        control = (mv.get("control") or "").lower()
        if control not in ("heater", "fan") or mv.get("percentage") is None:
            continue
        rel = float(mv.get("rel_time", 0))
        if rel <= 1.0 and mv.get("marker") == "CHARGE":
            continue
        logged.append({"control": control, "value": _snap10(mv["percentage"]),
                       "rel_time": rel, "bt": mv.get("bt"), "used": False})

    def match(control, value, at):
        cands = [m for m in logged if not m["used"] and m["control"] == control
                 and m["value"] == value]
        if not cands:
            return None
        best = min(cands, key=lambda m: abs(m["rel_time"] - (at if at is not None else m["rel_time"])))
        best["used"] = True
        return best

    rows = []
    for mv in schedule.get("moves", []):
        at = expected_seconds(mv, envelope)
        bt = expected_bt(mv, envelope)
        hit = match(mv["action"], mv["value"], at)
        row = {"id": mv.get("id"), "action": mv["action"], "value": mv["value"],
               "expected_s": at, "expected_bt": bt, "missed": hit is None}
        if hit:
            row["executed_s"] = hit["rel_time"]
            row["executed_bt"] = hit["bt"]
            row["delta_s"] = None if at is None else round(hit["rel_time"] - at)
            row["delta_f"] = (None if bt is None or hit["bt"] is None
                              else round(hit["bt"] - bt))
        rows.append(row)
    for mv in schedule.get("post_fc", []):
        at = fc_time + float(mv["trigger"]["seconds"]) if fc_time else None
        hit = match(mv["action"], mv["value"], at)
        row = {"id": mv.get("id"), "action": mv["action"], "value": mv["value"],
               "expected_s": at, "expected_bt": None, "missed": hit is None, "post_fc": True}
        if hit:
            row["executed_s"] = hit["rel_time"]
            row["executed_bt"] = hit["bt"]
            row["delta_s"] = None if at is None else round(hit["rel_time"] - at)
            row["delta_f"] = None
        rows.append(row)
    extra = [{"action": m["control"], "value": m["value"], "at_s": m["rel_time"], "bt": m["bt"]}
             for m in logged if not m["used"]]

    pred = schedule.get("predicted") or {}
    dev_designed = schedule.get("drop", {}).get("seconds")
    dev_actual = metrics.get("dev_phase_time") or None
    result = {
        "moves": rows,
        "extra": extra,
        "missed": sum(1 for r in rows if r["missed"]),
        "dev": {"designed_s": dev_designed, "actual_s": dev_actual,
                "delta_s": (None if dev_actual is None or dev_designed is None
                            else round(dev_actual - dev_designed))},
        "fc": {"predicted_s": pred.get("fc_time_s"), "actual_s": fc_time or None,
               "predicted_bt": pred.get("fc_bt"), "actual_bt": metrics.get("fc_bt") or None},
        "drop": {"predicted_bt": pred.get("drop_bt"), "actual_bt": metrics.get("drop_bt") or None},
    }
    result["summary"] = _execution_summary(result)
    return result


def _execution_summary(res):
    """One line for the prompt and the summary box."""
    deltas = [abs(r["delta_s"]) for r in res["moves"] if r.get("delta_s") is not None]
    bits = []
    if res["moves"]:
        bits.append(f"{len(res['moves']) - res['missed']}/{len(res['moves'])} moves executed")
        if deltas:
            bits.append(f"max {max(deltas):.0f}s off schedule")
    if res["extra"]:
        bits.append(f"{len(res['extra'])} unplanned move(s)")
    d = res["dev"]
    if d["delta_s"] is not None:
        bits.append(f"dev {d['actual_s']:.0f}s vs {d['designed_s']}s designed")
    f = res["fc"]
    if f["predicted_s"] and f["actual_s"]:
        bits.append(f"FC {fmt_mmss(f['actual_s'])} vs {fmt_mmss(f['predicted_s'])} predicted")
    return "; ".join(bits) if bits else "no designed moves to compare"


# ------------------------------------------------------------ presentation

def schedule_rows(schedule, envelope=None):
    """Flat rows for the display and the session sheet, in roast order."""
    rows = [{"phase": "charge", "when": "0:00", "seconds": 0,
             "bt": schedule.get("charge", {}).get("bt"),
             "action": "charge", "value": None, "kind": "manual",
             "text": f"CHARGE at {schedule.get('charge', {}).get('bt', 300)}F, heater "
                     f"{schedule.get('charge', {}).get('heater', 100)}%, fan "
                     f"{schedule.get('charge', {}).get('fan', 0)}%",
             "why": ""}]
    for mv in schedule.get("moves", []):
        secs = expected_seconds(mv, envelope)
        bt = expected_bt(mv, envelope)
        kind = trigger_kind(mv["trigger"])
        trig = (f"BT {mv['trigger']['bt']}F" if kind == "bt"
                else f"{mv['trigger']['after']} + {fmt_mmss(mv['trigger']['seconds'])}")
        rows.append({"phase": "pre-FC", "when": fmt_mmss(secs), "seconds": secs,
                     "bt": None if bt is None else round(bt), "action": mv["action"],
                     "value": mv["value"], "kind": kind,
                     "text": f"{mv['action'].capitalize()} -> {mv['value']}% at {trig}",
                     "why": mv.get("why", "")})
    stats = (envelope or {}).get("fc_stats") or {}
    fc_s = stats.get("fc_time_mean") or (schedule.get("predicted") or {}).get("fc_time_s")
    for mv in schedule.get("post_fc", []):
        after = mv["trigger"]["seconds"]
        rows.append({"phase": "post-FC", "when": f"FC+{fmt_mmss(after)}",
                     "seconds": None if not fc_s else fc_s + after, "bt": None,
                     "action": mv["action"], "value": mv["value"], "kind": "time",
                     "text": f"{mv['action'].capitalize()} -> {mv['value']}% at FC + {fmt_mmss(after)}",
                     "why": mv.get("why", "")})
    dev = schedule.get("drop", {}).get("seconds", 0)
    rows.append({"phase": "drop", "when": f"FC+{fmt_mmss(dev)}",
                 "seconds": None if not fc_s else fc_s + dev,
                 "bt": (schedule.get("predicted") or {}).get("drop_bt"),
                 "action": "drop", "value": None, "kind": "time",
                 "text": f"DROP at FC + {fmt_mmss(dev)}", "why": ""})
    return rows


def staircase_summary(schedule):
    """Compact one-line text of the heater/fan plan, for prompts and logs."""
    parts = [f"charge H{schedule.get('charge', {}).get('heater', 100)}/F{schedule.get('charge', {}).get('fan', 0)}"]
    for mv in schedule.get("moves", []):
        kind = trigger_kind(mv["trigger"])
        at = (f"@{mv['trigger']['bt']}F" if kind == "bt"
              else f"@{fmt_mmss(mv['trigger']['seconds'])}")
        parts.append(f"{mv['action'][0].upper()}{mv['value']}{at}")
    for mv in schedule.get("post_fc", []):
        parts.append(f"{mv['action'][0].upper()}{mv['value']}@FC+{mv['trigger']['seconds']}s")
    parts.append(f"DROP@FC+{schedule.get('drop', {}).get('seconds', 0)}s")
    return " ".join(parts)


def median_or_none(values):
    """Median of the non-None values, or None."""
    vals = [v for v in values if v is not None]
    return median(vals) if vals else None
