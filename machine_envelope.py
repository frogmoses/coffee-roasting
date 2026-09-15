"""Machine envelope: what this Hottop measurably does, from the roast history.

The profile designer has to work inside what the machine has actually done,
not inside roasting theory. This module reduces a bean's logged roasts to the
numbers a design needs: the heater/fan staircase as it was really executed
(median BT and time of each level), the median BT-vs-time curve (so a BT
trigger can be translated into an expected time and back), first-crack
statistics and the post-FC rise (from `roast_plan`), the same-day heat-soak
shift, the ambient readings, the development history with its cupping
verdicts, and the ear's measured alert latency.

A new bean has no history. `borrow_envelope` takes the most recent bean with
enough roasts: the machine, the 300F charge, and the warm-up routine are the
same, so the staircase and the curve transfer, with first crack uncertain by
about a minute. The envelope carries `borrowed_from` so the designer, the
validator, and the sheet all say so.

History does not store control moves, so the envelope re-parses each
roast's `.alog` (the same thing `roast_plan` and `refresh_recommendations`
do); a roast whose file is gone is skipped, not fatal.
"""

from statistics import median

from roast_parser import parse_alog, extract_roast_data
from roast_narrative import build_control_timeline
from roast_metrics import build_curve_series
from roast_plan import same_bean, fc_stats, batch_position_shift
from crack_loader import find_crack_logs
from sentinel_loader import load_json_cached

# Ear alert latency when no sidecar has a live verdict yet (measured 17-18 s
# on 2026-09-13: the 7-in-20 s rule needs the window to fill).
DEFAULT_EAR_LATENCY_S = 18.0
# Curve rows are bucketed to this many seconds when taking the median.
CURVE_STEP_S = 30
# A borrowed envelope needs at least this many roasts to mean anything.
MIN_BORROW_ROASTS = 3

_PARSE_ERRORS = (ValueError, FileNotFoundError, KeyError, IndexError, TypeError)


def _median(values):
    """Median of the truthy numeric values, or None."""
    vals = [v for v in values if v]
    return median(vals) if vals else None


def measured_staircase(timelines):
    """Median time and BT of every (control, level) the roasts actually set.

    Returns a list of {control, value, median_time, median_bt, n} ordered by
    median time, plus the median charge heater/fan under "start". The 0:00
    [CHARGE] row is the charge state and is not a move.
    """
    groups = {}
    starts_h, starts_f = [], []
    for tl in timelines:
        if tl.get("start_heater") is not None:
            starts_h.append(tl["start_heater"])
        if tl.get("start_fan") is not None:
            starts_f.append(tl["start_fan"])
        for mv in tl.get("moves", []):
            control = (mv.get("control") or "").lower()
            if control not in ("heater", "fan") or mv.get("percentage") is None:
                continue
            rel = float(mv.get("rel_time", 0))
            if rel <= 1.0 and mv.get("marker") == "CHARGE":
                continue
            key = (control, int(round(mv["percentage"])))
            groups.setdefault(key, []).append((rel, mv.get("bt")))
    rows = []
    for (control, value), hits in groups.items():
        rows.append({
            "control": control, "value": value,
            "median_time": round(median(h[0] for h in hits), 1),
            "median_bt": _median([h[1] for h in hits]),
            "n": len(hits),
        })
    rows.sort(key=lambda r: r["median_time"])
    return {"start": {"heater": _median(starts_h), "fan": _median(starts_f)}, "moves": rows}


def median_curve(curves, step=CURVE_STEP_S):
    """Median BT per `step`-second bucket across several roasts' curves.

    Buckets present in fewer than half the curves are dropped so the tail
    (the longest roast alone) does not pull the median around.
    """
    if not curves:
        return []
    buckets = {}
    for curve in curves:
        seen = set()
        for row in curve:
            b = int(round(row["time"] / step)) * step
            if b in seen:
                continue
            seen.add(b)
            buckets.setdefault(b, []).append(row["bt"])
    need = max(1, len(curves) // 2)
    out = [{"time": b, "bt": round(median(v), 1)} for b, v in sorted(buckets.items()) if len(v) >= need]
    return out


def ear_latency(captures_dir=None):
    """(median seconds from FC onset to the ear's declaration, sample count).

    Read from the crack sidecars' live verdicts; falls back to the measured
    default when none has declared yet.
    """
    lat = []
    try:
        paths = find_crack_logs(captures_dir)
    except OSError:
        paths = []
    for _session_id, path in paths:
        try:
            data = load_json_cached(path)
        except (OSError, ValueError):
            continue
        fc = (data or {}).get("fc_detected") or {}
        if fc.get("declared_at_elapsed") is not None and fc.get("elapsed") is not None:
            lat.append(float(fc["declared_at_elapsed"]) - float(fc["elapsed"]))
    if not lat:
        return DEFAULT_EAR_LATENCY_S, 0
    return round(median(lat), 1), len(lat)


def _dev_history(entries):
    """Development seconds and cupping verdict per roast, oldest first."""
    rows = []
    for e in entries:
        m = e.get("metrics", {})
        intake = e.get("cupping_intake") or {}
        notes = e.get("cupping_notes") or ""
        rows.append({
            "batch": e.get("batch_nr", 0), "date": e.get("roast_date", ""),
            "dev_s": m.get("dev_phase_time") or None,
            "fc_bt": m.get("fc_bt") or None, "drop_bt": m.get("drop_bt") or None,
            "fc_time": m.get("fc_time") or None,
            "ambient_temp": m.get("ambient_temp") or None,
            "balance": intake.get("balance"), "astringency": intake.get("astringency"),
            "sweetness": intake.get("sweetness"), "body": intake.get("body"),
            "preference": intake.get("preference"), "drink_again": intake.get("drink_again"),
            "notes": (notes[:160] + "...") if len(notes) > 160 else notes,
            "cupped": bool(intake or notes),
        })
    return rows


def build_envelope(history, title, limit=6, captures_dir=None):
    """The envelope for one bean from its history (see module docstring).

    `limit` bounds how many recent roasts are re-parsed for the staircase
    and the curve; FC statistics use every roast of the bean.
    """
    entries = same_bean(history, title)
    recent = [e for e in entries if e.get("source_file")][-limit:]
    timelines, curves, used = [], [], []
    for e in recent:
        try:
            data = extract_roast_data(parse_alog(e["source_file"]))
        except _PARSE_ERRORS:
            continue
        timelines.append(build_control_timeline(data))
        curve = build_curve_series(data, step=CURVE_STEP_S)
        if curve:
            curves.append(curve)
        used.append(e.get("roast_id", ""))
    metrics = [e.get("metrics", {}) for e in recent]
    stats = fc_stats(entries)
    latency, latency_n = ear_latency(captures_dir)
    env = {
        "bean": title,
        "count": len(entries),
        "source_roasts": used,
        "staircase": measured_staircase(timelines),
        "median_curve": median_curve(curves),
        "charge_bt": _median([m.get("charge_bt") for m in metrics]),
        "tp_bt": _median([m.get("tp_bt") for m in metrics]),
        "tp_time": _median([m.get("tp_time") for m in metrics]),
        "dry_time": _median([m.get("dry_phase_time") for m in metrics]),
        "fc_stats": stats,
        "fc_time": stats.get("fc_time_mean") or None,
        "fc_bt": stats.get("fc_bt_mean") or None,
        "ror_at_fc": _median([m.get("ror_at_fc") for m in metrics]),
        "heat_soak_shift": batch_position_shift(entries),
        "ambient": [{"date": e.get("roast_date", ""), "batch": e.get("batch_nr", 0),
                     "ambient_temp": e["metrics"]["ambient_temp"],
                     "fc_time": e["metrics"].get("fc_time")}
                    for e in entries if e.get("metrics", {}).get("ambient_temp")],
        "dev_history": _dev_history(entries),
        "ear_latency_s": latency,
        "ear_latency_n": latency_n,
        "borrowed_from": None,
    }
    return env


def borrow_envelope(history, exclude_title, min_roasts=MIN_BORROW_ROASTS, limit=6,
                    captures_dir=None):
    """Envelope of the most recently roasted bean with enough history.

    For a bean that has never been roasted here. Returns None when no bean
    qualifies. The result carries `borrowed_from`.
    """
    skip = (exclude_title or "").strip().lower()
    candidates = {}
    for e in history.values():
        title = (e.get("title") or "").strip()
        if not title or title.lower() == skip:
            continue
        candidates.setdefault(title, []).append(e)
    best, best_date = None, ""
    for title, entries in candidates.items():
        if len(entries) < min_roasts:
            continue
        latest = max(e.get("roast_date", "") for e in entries)
        if latest > best_date:
            best, best_date = title, latest
    if best is None:
        return None
    env = build_envelope(history, best, limit=limit, captures_dir=captures_dir)
    env["borrowed_from"] = best
    env["bean"] = exclude_title
    return env


def envelope_for(history, title, limit=6, captures_dir=None):
    """Own envelope when the bean has usable history, else a borrowed one."""
    env = build_envelope(history, title, limit=limit, captures_dir=captures_dir)
    if env["count"] and env["median_curve"]:
        return env
    borrowed = borrow_envelope(history, title, limit=limit, captures_dir=captures_dir)
    return borrowed or env


def compact_envelope(env, curve_step=60):
    """A smaller copy for prompts and profile snapshots (curve thinned,
    long notes dropped)."""
    out = dict(env)
    out["median_curve"] = [r for r in env.get("median_curve", [])
                           if int(r["time"]) % curve_step == 0]
    out["dev_history"] = [dict(r, notes=(r.get("notes") or "")[:80]) for r in env.get("dev_history", [])]
    return out
