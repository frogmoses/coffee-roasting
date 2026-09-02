"""Contrast planner: turn one recipe into a set of deliberately different batches.

Three batches roasted the same way teach nothing — the roaster's own logs
show batch-to-batch FC time drifting by a minute under an identical manual
schedule, so identical batches taste identical to within noise. A contrast
set holds everything through first crack the same and varies ONE lever, the
seconds from FC to DROP, across a bracket wide enough to taste
(under / current / over). Tasting the bracket is how an untrained palate
learns what "sour" and "roasty" mean for this bean; the middle becomes
describable by what it isn't.

Why FC->DROP seconds and not drop temperature or a pre-FC change:
- It is the one lever the roaster already times deliberately.
- Timing from FC insulates the experiment from the machine's heat-soak
  drift: a warmer drum moves FC earlier, but "N seconds after FC" still
  means the same thing.
- Drop temperature is an outcome; the planner projects it only to keep the
  long batch clear of the 408F safety eject.

The plan is deterministic (no LLM) so it can be printed and taken to the
roaster. The base schedule is reconstructed from the most recent roast of
the bean (its actual heater/fan moves), and the FC statistics come from
every roast of the bean in history.
"""

from statistics import mean, pstdev

from roast_metrics import SAFETY_EJECT_BT, _fmt_time
from roast_narrative import build_control_timeline
from roast_parser import parse_alog, extract_roast_data

# Default bracket, in ROASTING order: the roaster's current practice (~150s)
# first, so that batch is roasted under the same cold-drum conditions as every
# earlier first-of-day batch in history and stays comparable; then the short
# and long ends, +/- 60s — a full minute is far above the noise floor.
DEFAULT_DEV_TIMES = (150, 90, 210)

# Fallback post-FC BT rise when the bean's history can't supply one. Measured
# Aug 2026 on this Hottop at heater 60-70%: ~5F per 30s, close to linear.
DEFAULT_POST_FC_RISE_F_PER_MIN = 9.5

# Keep the projected drop at least this far under the safety eject.
EJECT_MARGIN_F = 10


def _same_bean(history, title):
    """All history entries for this bean title, ordered by (date, batch)."""
    name = (title or "").strip().lower()
    entries = [
        e for e in history.values()
        if (e.get("title", "") or "").strip().lower() == name
    ]
    entries.sort(key=lambda e: (e.get("roast_date", ""), e.get("batch_nr", 0)))
    return entries


def _fc_stats(entries):
    """FC time / BT statistics and post-FC rise rate across the bean's roasts."""
    fc_times = [e["metrics"]["fc_time"] for e in entries
                if e.get("metrics", {}).get("fc_time", 0) > 0]
    fc_bts = [e["metrics"]["fc_bt"] for e in entries
              if e.get("metrics", {}).get("fc_bt", 0) > 0]
    rates = []
    for e in entries:
        m = e.get("metrics", {})
        if m.get("fc_bt", 0) > 0 and m.get("drop_bt", 0) > m["fc_bt"] and m.get("dev_phase_time", 0) > 0:
            rates.append((m["drop_bt"] - m["fc_bt"]) / (m["dev_phase_time"] / 60.0))
    return {
        "count": len(fc_times),
        "fc_time_mean": mean(fc_times) if fc_times else 0,
        "fc_time_sd": pstdev(fc_times) if len(fc_times) > 1 else 0,
        "fc_time_min": min(fc_times) if fc_times else 0,
        "fc_time_max": max(fc_times) if fc_times else 0,
        "fc_bt_mean": mean(fc_bts) if fc_bts else 0,
        "post_fc_rise": mean(rates) if rates else DEFAULT_POST_FC_RISE_F_PER_MIN,
        "post_fc_rise_measured": bool(rates),
    }


def _batch_position_shift(entries):
    """Mean FC-time shift of later same-day batches vs the day's first batch.

    Negative = later batches reach FC sooner (drum heat-soak). None if the
    history has no multi-batch days.
    """
    by_date = {}
    for e in entries:
        if e.get("metrics", {}).get("fc_time", 0) > 0:
            by_date.setdefault(e.get("roast_date", ""), []).append(e)
    shifts = []
    for day in by_date.values():
        if len(day) < 2:
            continue
        first = day[0]["metrics"]["fc_time"]
        shifts += [e["metrics"]["fc_time"] - first for e in day[1:]]
    return mean(shifts) if shifts else None


def _base_schedule(anchor):
    """Split the anchor roast's control moves into through-FC and after-FC.

    Re-parses the anchor's .alog for the timeline. Returns
    (pre_fc_moves, post_fc_moves, error) where post-FC moves carry
    `after_fc` seconds instead of absolute time, and error is a message when
    the timeline could not be reconstructed.
    """
    source = anchor.get("source_file", "")
    if not source:
        return [], [], "no source .alog recorded for the anchor roast"
    try:
        data = extract_roast_data(parse_alog(source))
    except (ValueError, FileNotFoundError, KeyError, IndexError, TypeError) as e:
        return [], [], f"could not re-read {source}: {e}"
    timeline = build_control_timeline(data)
    fc_time = anchor.get("metrics", {}).get("fc_time", 0)
    pre, post = [], []
    for mv in timeline.get("moves", []):
        if fc_time and mv["rel_time"] > fc_time:
            post.append(dict(mv, after_fc=mv["rel_time"] - fc_time))
        else:
            pre.append(mv)
    start = {"heater": timeline.get("start_heater"), "fan": timeline.get("start_fan")}
    return {"start": start, "moves": pre}, post, None


def build_contrast_plan(history, roast_id, dev_times=DEFAULT_DEV_TIMES):
    """Build a contrast set for the bean of `roast_id`.

    Args:
        history: Full roast history dict.
        roast_id: Anchor roast (its schedule is the shared recipe).
        dev_times: FC->DROP seconds for each batch, in the order to roast.

    Returns:
        Plan dict: bean, anchor batch, fc stats, base schedule, post-FC moves,
        per-batch contrasts (dev seconds, projected drop BT, safety margin),
        heat-soak shift, and notes. Never raises on thin history — the
        contrasts still project from the defaults.
    """
    anchor = history[roast_id]
    title = anchor.get("title", "")
    entries = _same_bean(history, title) or [anchor]
    stats = _fc_stats(entries)
    shift = _batch_position_shift(entries)
    base, post_fc, schedule_error = _base_schedule(anchor)

    fc_bt = stats["fc_bt_mean"] or anchor.get("metrics", {}).get("fc_bt", 0) or 360
    contrasts = []
    for order, dev_s in enumerate(dev_times, 1):
        projected = fc_bt + stats["post_fc_rise"] * dev_s / 60.0
        margin = SAFETY_EJECT_BT - projected
        contrasts.append({
            "order": order,
            "dev_s": dev_s,
            "label": _label_for(dev_s, dev_times),
            "projected_drop_bt": round(projected),
            "eject_margin": round(margin),
            "unsafe": margin < EJECT_MARGIN_F,
        })

    notes = []
    if schedule_error:
        notes.append(f"Base schedule unavailable: {schedule_error}")
    if stats["count"] >= 2:
        notes.append(
            f"FC has landed between {_fmt_time(stats['fc_time_min'])} and "
            f"{_fmt_time(stats['fc_time_max'])} on this recipe (sd {stats['fc_time_sd']:.0f}s). "
            "Time the drop from FC, not from CHARGE."
        )
    if shift is not None and shift < -10:
        notes.append(
            f"Later batches on the same day reach FC ~{abs(shift):.0f}s sooner (drum heat-soak). "
            "Warm the machine longer before batch 1 and hold a fixed gap between batches."
        )
    unsafe = [c for c in contrasts if c["unsafe"]]
    if unsafe:
        notes.append(
            "Projected drop within "
            f"{EJECT_MARGIN_F}F of the {SAFETY_EJECT_BT}F safety eject for dev "
            + ", ".join(str(c["dev_s"]) + "s" for c in unsafe)
            + " — shorten it or cut the heater harder after FC."
        )
    return {
        "bean": title,
        "anchor_roast_id": roast_id,
        "anchor_batch": anchor.get("batch_nr", 0),
        "anchor_date": anchor.get("roast_date", ""),
        "fc_stats": stats,
        "heat_soak_shift": shift,
        "base_schedule": base if not schedule_error else {"start": {}, "moves": []},
        "post_fc_moves": post_fc,
        "contrasts": contrasts,
        "notes": notes,
    }


def _label_for(dev_s, dev_times):
    """Name each batch by where it sits in the bracket."""
    ordered = sorted(dev_times)
    if len(ordered) == 1:
        return "single"
    if dev_s == ordered[0]:
        return "short: expect brighter/sourer"
    if dev_s == ordered[-1]:
        return "long: expect roastier/heavier"
    return "control: current practice"


def parse_dev_times(text):
    """Parse a CLI '--dev 90,150,210' string into a tuple of ints."""
    try:
        times = tuple(int(x) for x in text.split(",") if x.strip())
    except ValueError:
        raise ValueError(f"--dev expects comma-separated seconds, got {text!r}")
    if not times or any(t <= 0 for t in times):
        raise ValueError("--dev needs one or more positive seconds")
    return times
