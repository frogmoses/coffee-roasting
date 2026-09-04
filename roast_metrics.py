"""Metric extraction and RoR analysis for coffee roasts.

Extracts the facts of a roast (phase times/percentages, key temperatures,
rate-of-rise shape, weight loss) for the Hottop KN-8828B-2K+ (manual mode,
~300F charge, drop timed from first crack). There are no numeric "target
bands" — judging a roast against unvalidated target ranges was removed in
favor of reasoning from roasting theory and the bean's intended flavor
(see llm_recommender.py). The only fixed reference kept here is the machine
safety point below.
"""

from statistics import mean

# Hottop hard safety point — the machine ejects beans at this BT unless the
# ENTER button is held. Empirically 408F on this machine (the manual's 395F
# figure is wrong). Not a taste target — a physical safety ceiling.
SAFETY_EJECT_BT = 408

# Curve-based first-crack detection looks for the steam-release RoR crash
# inside this BT band. It is a prior: on this machine every by-ear FC mark
# (13 roasts, three beans) fell between 358F and 366F, and outside the band
# the late-development RoR nosedive (~380F) looks identical to the crack.
FC_DETECT_BT_BAND = (350, 372)
# The crash is measured as the RoR change over this many seconds.
FC_DETECT_DROP_SPAN = 20
# A mark this far (seconds) from the curve's crash is flagged as suspect.
FC_MARK_TOLERANCE = 30


def get_phase_percentages(computed):
    """Calculate drying/maillard/development phase percentages.

    Args:
        computed: The 'computed' dict from the .alog data.

    Returns:
        Dict with phase percentages and times.
    """
    total = computed.get("totaltime", 0)
    if total == 0:
        return {}

    dry_time = computed.get("dryphasetime", 0)
    mid_time = computed.get("midphasetime", 0)
    finish_time = computed.get("finishphasetime", 0)

    return {
        "dry_phase_pct": round(dry_time / total * 100, 1),
        "mid_phase_pct": round(mid_time / total * 100, 1),
        "dev_phase_pct": round(finish_time / total * 100, 1),
        "dry_phase_time": dry_time,
        "mid_phase_time": mid_time,
        "dev_phase_time": finish_time,
        "total_time": total,
    }


def count_heat_adjustments(data):
    """Count heater events between CHARGE and DROP.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().

    Returns:
        Number of heater adjustments during the roast.
    """
    timeindex = data.get("timeindex", [])
    if len(timeindex) < 7:
        return 0

    charge_idx = timeindex[0]
    drop_idx = timeindex[6]
    if drop_idx == 0:
        # DROP not recorded, use last data point
        drop_idx = len(data.get("timex", [])) - 1

    count = 0
    for event in data.get("events", []):
        # type 3 = Heater
        if event["type"] == 3 and charge_idx <= event["index"] <= drop_idx:
            count += 1

    return count


def assess_ror_smoothness(data, heat_adjustment_count=0):
    """Check the Rate of Rise curve for oscillation and FC crash/flick.

    BT is smoothed with a light (~10s) centered moving average before RoR
    is computed, so the raw probe's quantization staircase doesn't read as
    phantom oscillation or crash/flick.

    Excludes drying phase from oscillation counting since TP recovery
    naturally causes direction changes that aren't meaningful oscillation.
    Falls back to full-window analysis if DRY event wasn't recorded.

    Also detects the classic first-crack defects (Rao/Cropster):
    - Crash: RoR plunges toward stall right after FC (bakes the coffee)
    - Flick: RoR climbs back up after a meaningful post-FC sag (chars the
      coffee). A small rebound off a curve that never really declined is
      normal thermal noise, not a flick.

    Args:
        data: Extracted roast data.
        heat_adjustment_count: Number of heater events (for correlation).

    Returns:
        Dict with smoothness assessment: oscillation count, severity,
        phase-segmented counts, heat correlation, crash/flick flags,
        and details.
    """
    bt = data.get("bt", [])
    timex = data.get("timex", [])
    timeindex = data.get("timeindex", [])

    if len(bt) < 10 or len(timeindex) < 7:
        return {"oscillations": 0, "severity": "unknown", "details": "Insufficient data"}

    charge_idx = max(timeindex[0], 0)  # -1 means CHARGE not set
    drop_idx = timeindex[6] if timeindex[6] > 0 else len(bt) - 1
    # Phase boundaries: DRY (index 1) and FCs (index 2)
    dry_idx = timeindex[1] if len(timeindex) > 1 else 0
    fc_idx = timeindex[2] if len(timeindex) > 2 else 0

    # Derive the smoothing window from the actual sampling interval so the
    # ~30-second RoR window holds even if Artisan's sampling rate changes
    deltas = [
        timex[i] - timex[i - 1]
        for i in range(charge_idx + 1, min(charge_idx + 50, len(timex)))
    ]
    deltas = [d for d in deltas if d > 0]
    interval = sorted(deltas)[len(deltas) // 2] if deltas else 2.0
    window = max(3, round(30.0 / interval))

    # Artisan logs raw BT, quantized to a coarse probe grid (~0.3-0.6F steps
    # plus occasional spikes). Differencing that staircase directly turns
    # quantization into phantom RoR oscillation and false crash/flick wobble.
    # Smooth BT with a light centered moving average (~10s, well under the
    # 30s RoR window) before computing RoR — enough to remove the staircase
    # without flattening a real crash or flick. All RoR analysis below reads
    # from this smoothed curve.
    smooth_half = max(1, round(5.0 / interval))  # ~10s total span

    def _smooth(values):
        """Centered moving average over ~10s, robust to short index ranges."""
        out = []
        n = len(values)
        for i in range(n):
            lo = max(0, i - smooth_half)
            hi = min(n, i + smooth_half + 1)
            out.append(sum(values[lo:hi]) / (hi - lo))
        return out

    bt_s = _smooth(bt)

    def _count_direction_changes(ror_vals):
        """Count significant direction changes in a RoR segment."""
        changes = 0
        for i in range(2, len(ror_vals)):
            prev_delta = ror_vals[i - 1] - ror_vals[i - 2]
            curr_delta = ror_vals[i] - ror_vals[i - 1]
            # Only count significant changes (> 1 F/min swing)
            if prev_delta * curr_delta < 0 and abs(curr_delta - prev_delta) > 2:
                changes += 1
        return changes

    def _calc_ror_points(start_idx, end_idx):
        """Calculate (time, RoR) pairs for a data index range, from smoothed BT."""
        points = []
        for i in range(max(start_idx, charge_idx + window), min(end_idx + 1, len(bt_s))):
            if i >= len(bt_s) or (i - window) < 0:
                continue
            dt = timex[i] - timex[i - window]
            if dt > 0:
                ror = (bt_s[i] - bt_s[i - window]) / dt * 60  # F/min
                points.append((timex[i], ror))
        return points

    def _max_sustained_rise(points):
        """Largest sustained climb in a RoR segment: (magnitude F/min, seconds).

        Walks the series tracking the gain from the lowest preceding RoR
        (running trough) to each later point, returning the biggest such
        trough->peak gain and how long it took. A continuously declining
        curve keeps setting new troughs, so the gain stays ~0; a curve that
        climbs back up returns the size and duration of that climb. This is
        what flags a violation of Rao's second rule (an ever-decelerating
        bean temp) — distinct from oscillation (wobble) and from the post-FC
        flick (a point event handled separately below).
        """
        if len(points) < 3:
            return 0.0, 0.0
        trough_v = points[0][1]
        trough_t = points[0][0]
        best_rise = 0.0
        best_dur = 0.0
        for t, v in points:
            if v < trough_v:
                trough_v = v
                trough_t = t
            elif v - trough_v > best_rise:
                best_rise = v - trough_v
                best_dur = t - trough_t
        return best_rise, best_dur

    # If DRY event was recorded, do phase-segmented analysis
    use_phases = dry_idx > 0

    # Deceleration (Rao's 2nd rule): the RoR should fall continuously through
    # Maillard. A sustained climb here means heat went in too late. Only
    # meaningful with phase structure — in the fallback (no DRY) the drying
    # climb can't be excluded, so it's left at zero.
    maillard_rise_mag = 0.0
    maillard_rise_dur = 0.0

    if use_phases:
        # Maillard phase: DRY -> FCs (or DROP if FCs not recorded)
        maillard_end = fc_idx if fc_idx > 0 else drop_idx
        maillard_points = _calc_ror_points(dry_idx, maillard_end)
        maillard_ror = [v for _, v in maillard_points]
        maillard_osc = _count_direction_changes(maillard_ror) if len(maillard_ror) >= 3 else 0
        maillard_rise_mag, maillard_rise_dur = _max_sustained_rise(maillard_points)

        # Development phase: FCs -> DROP
        dev_osc = 0
        dev_points = []
        if fc_idx > 0:
            dev_points = _calc_ror_points(fc_idx, drop_idx)
            dev_ror = [v for _, v in dev_points]
            dev_osc = _count_direction_changes(dev_ror) if len(dev_ror) >= 3 else 0

        direction_changes = maillard_osc + dev_osc
        all_points = maillard_points + dev_points

        # Lower thresholds since drying is excluded
        if direction_changes <= 2:
            severity = "smooth"
        elif direction_changes <= 4:
            severity = "moderate"
        else:
            severity = "oscillating"
    else:
        # Fallback: full-window analysis (DRY not recorded)
        all_points = _calc_ror_points(charge_idx, drop_idx)
        all_ror_vals = [v for _, v in all_points]
        direction_changes = _count_direction_changes(all_ror_vals) if len(all_ror_vals) >= 3 else 0
        maillard_osc = 0
        dev_osc = 0

        # Original thresholds for full-window
        if direction_changes <= 3:
            severity = "smooth"
        elif direction_changes <= 6:
            severity = "moderate"
        else:
            severity = "oscillating"

    all_ror = [v for _, v in all_points]
    if len(all_ror) < 5:
        return {"oscillations": 0, "severity": "unknown", "details": "Too few RoR points"}

    # First-crack crash/flick detection. Crash = RoR plunges to near-stall
    # within ~90s after FC; flick = RoR climbs back up after a *meaningful*
    # post-FC sag (the char signature). The rebound alone isn't enough — a
    # gently wobbling curve that never really declined (e.g. FC 11 -> 8 -> 14)
    # is normal thermal noise, not a flick, so require the dip to drop the RoR
    # at least FLICK_MIN_SAG below its FC value before the rebound counts.
    # Thresholds are heuristics for this machine.
    FLICK_MIN_SAG = 5.0   # F/min the RoR must fall before a rebound is a flick
    FLICK_MIN_REBOUND = 3.0  # F/min the RoR must climb back off the minimum
    fc_crash = False
    fc_flick = False
    crash_min_ror = None
    if fc_idx > 0 and drop_idx > fc_idx and 0 <= fc_idx < len(timex):
        fc_time_abs = timex[fc_idx]
        pre_fc = [v for t, v in all_points if t <= fc_time_abs]
        post_fc = [(t, v) for t, v in all_points if fc_time_abs < t <= fc_time_abs + 90]
        if pre_fc and len(post_fc) >= 3:
            ror_at_fc = pre_fc[-1]
            min_t, min_v = min(post_fc, key=lambda p: p[1])
            sag = ror_at_fc - min_v  # how far the RoR fell after FC
            if sag >= 8 and min_v < 5:
                fc_crash = True
                crash_min_ror = round(min_v, 1)
            after_min = [v for t, v in post_fc if t > min_t]
            if (sag >= FLICK_MIN_SAG
                    and after_min
                    and max(after_min) - min_v >= FLICK_MIN_REBOUND):
                fc_flick = True

    # Deceleration violation (Rao's 2nd rule): a sustained Maillard RoR climb.
    # Require both a meaningful magnitude and a duration past the ~30s RoR
    # window so a brief blip or quantization wobble doesn't trip it.
    DECEL_MIN_RISE = 4.0       # F/min the RoR must climb to count as rising
    DECEL_MIN_DURATION = 40.0  # seconds the climb must be sustained
    ror_rising = (
        maillard_rise_mag >= DECEL_MIN_RISE
        and maillard_rise_dur >= DECEL_MIN_DURATION
    )

    # Heat correlation: how many heat inputs vs oscillation output.
    # <= 4 is within the heat_adjustments target, so oscillation alongside
    # few inputs reads as natural thermal behavior rather than over-control.
    if heat_adjustment_count <= 4:
        heat_correlation = "low_input"
    else:
        heat_correlation = "high_input"

    return {
        "oscillations": direction_changes,
        "maillard_oscillations": maillard_osc,
        "dev_oscillations": dev_osc,
        "severity": severity,
        "heat_correlation": heat_correlation,
        "fc_crash": fc_crash,
        "fc_flick": fc_flick,
        "crash_min_ror": crash_min_ror,
        "ror_rising": ror_rising,
        "ror_rise": round(maillard_rise_mag, 1),
        "ror_min": round(min(all_ror), 1),
        "ror_max": round(max(all_ror), 1),
        "ror_mean": round(mean(all_ror), 1),
        "details": f"{direction_changes} significant direction changes in RoR (post-drying)",
    }


def build_curve_series(data, step=30):
    """Downsample the CHARGE->DROP curve into (time, BT, RoR) rows.

    The crash/flick/rising flags from assess_ror_smoothness() are heuristics
    tuned for this machine; handing the LLM the (lightly smoothed) curve
    itself lets it verify or override them and spot shapes the flags don't
    encode. Emits a row roughly every `step` seconds plus one at each
    recorded phase boundary (CHARGE/DRY/FCs/DROP), using the same ~10s BT
    smoothing and ~30s RoR window as the smoothness analysis.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().
        step: Target seconds between rows.

    Returns:
        List of {"time": rel_seconds, "bt": F, "ror": F/min or None,
        "marker": "CHARGE"|"DRY"|"FCs"|"DROP"|""}. Empty list when there
        isn't enough data.
    """
    bt = data.get("bt", [])
    timex = data.get("timex", [])
    timeindex = data.get("timeindex", [])
    if len(bt) < 10 or len(timex) < 10 or len(timeindex) < 7:
        return []

    charge_idx = max(timeindex[0], 0)  # -1 means CHARGE not set
    drop_idx = timeindex[6] if timeindex[6] > 0 else len(bt) - 1
    drop_idx = min(drop_idx, len(bt) - 1, len(timex) - 1)
    if drop_idx <= charge_idx:
        return []

    # Same interval-derived windows as assess_ror_smoothness()
    deltas = [
        timex[i] - timex[i - 1]
        for i in range(charge_idx + 1, min(charge_idx + 50, len(timex)))
    ]
    deltas = [d for d in deltas if d > 0]
    interval = sorted(deltas)[len(deltas) // 2] if deltas else 2.0
    window = max(3, round(30.0 / interval))
    smooth_half = max(1, round(5.0 / interval))

    n = len(bt)
    bt_s = []
    for i in range(n):
        lo = max(0, i - smooth_half)
        hi = min(n, i + smooth_half + 1)
        bt_s.append(sum(bt[lo:hi]) / (hi - lo))

    # Phase boundary rows are always emitted, with a marker
    markers = {charge_idx: "CHARGE"}
    if len(timeindex) > 1 and timeindex[1] > 0:
        markers[timeindex[1]] = "DRY"
    if len(timeindex) > 2 and timeindex[2] > 0:
        markers[timeindex[2]] = "FCs"
    markers[drop_idx] = "DROP"

    charge_time = timex[charge_idx]
    rows = []
    next_t = 0.0
    for i in range(charge_idx, drop_idx + 1):
        rel = timex[i] - charge_time
        marker = markers.get(i, "")
        if rel < next_t and not marker:
            continue
        # RoR needs a full lookback window inside the roast
        ror = None
        if i - window >= charge_idx:
            dt = timex[i] - timex[i - window]
            if dt > 0:
                ror = round((bt_s[i] - bt_s[i - window]) / dt * 60, 1)
        rows.append({
            "time": rel,
            "bt": round(bt_s[i], 1),
            "ror": ror,
            "marker": marker,
        })
        if rel >= next_t:
            next_t = rel + step
    return rows


def detect_fc_from_curve(data, band=FC_DETECT_BT_BAND):
    """Locate first crack from the BT curve alone and grade the operator's mark.

    First crack releases steam, which cools the probe: the RoR drops 3-7
    F/min within ~20s of crack onset on this machine (seen on all 13 logged
    roasts). This finds the steepest sustained RoR drop while BT is inside
    `band` and reports how far the operator's FCs mark sits from it. On a
    quiet bean the mark can drift 20-40s; this is the after-the-fact check.

    Honest limits: the band is itself a fixed-temperature prior, the drop is
    small against a curve that is already falling, and heater cuts made
    60-90s earlier produce dips of similar size. Validated on this roaster's
    own logs: median offset +4s, 85% of marks within 30s. Treat it as a
    consistency check on the mark, not ground truth.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().
        band: (low_F, high_F) BT window to search.

    Returns:
        Dict with detected_time (rel. to CHARGE), detected_bt, ror_drop
        (F/min change over FC_DETECT_DROP_SPAN seconds, negative), mark_time
        (rel. to CHARGE, or None if FC was not marked), offset (detected -
        mark seconds, or None), mark_suspect (bool), and a details string —
        or None when the curve never enters the band before DROP.
    """
    bt = data.get("bt", [])
    timex = data.get("timex", [])
    timeindex = data.get("timeindex", [])
    if len(bt) < 10 or len(timex) < 10 or len(timeindex) < 7:
        return None

    charge_idx = max(timeindex[0], 0)
    drop_idx = timeindex[6] if timeindex[6] > 0 else len(bt) - 1
    drop_idx = min(drop_idx, len(bt) - 1, len(timex) - 1)
    fc_idx = timeindex[2] if len(timeindex) > 2 and timeindex[2] > 0 else None
    if drop_idx <= charge_idx:
        return None

    # Same interval-derived BT smoothing and 30s RoR window as the rest of
    # the RoR analysis, plus a light smoothing of the RoR itself so a single
    # quantization step in BT doesn't masquerade as the crash.
    deltas = [timex[i] - timex[i - 1]
              for i in range(charge_idx + 1, min(charge_idx + 50, len(timex)))]
    deltas = [d for d in deltas if d > 0]
    interval = sorted(deltas)[len(deltas) // 2] if deltas else 2.0
    window = max(3, round(30.0 / interval))
    smooth_half = max(1, round(5.0 / interval))
    span = max(2, round(FC_DETECT_DROP_SPAN / interval))

    n = len(bt)
    bt_s = []
    for i in range(n):
        lo = max(0, i - smooth_half)
        hi = min(n, i + smooth_half + 1)
        bt_s.append(sum(bt[lo:hi]) / (hi - lo))
    ror = [None] * n
    for i in range(window, n):
        dt = timex[i] - timex[i - window]
        if dt > 0:
            ror[i] = (bt_s[i] - bt_s[i - window]) / dt * 60
    ror_s = [None] * n
    for i in range(n):
        vals = [r for r in ror[max(0, i - smooth_half):i + smooth_half + 1] if r is not None]
        ror_s[i] = sum(vals) / len(vals) if vals else None

    low, high = band
    best_idx, best_drop = None, 0.0
    for i in range(charge_idx + window, drop_idx - span):
        if not (low <= bt[i] <= high):
            continue
        if ror_s[i] is None or ror_s[i + span] is None:
            continue
        drop = ror_s[i + span] - ror_s[i]
        if drop < best_drop:
            best_idx, best_drop = i, drop
    if best_idx is None:
        return None

    charge_time = timex[charge_idx]
    detected_time = timex[best_idx] - charge_time
    result = {
        "detected_time": round(detected_time),
        "detected_bt": round(bt[best_idx], 1),
        "ror_drop": round(best_drop, 1),
        "mark_time": None,
        "offset": None,
        "mark_suspect": False,
    }
    if fc_idx is not None and fc_idx < len(timex):
        mark_time = timex[fc_idx] - charge_time
        offset = detected_time - mark_time
        result["mark_time"] = round(mark_time)
        result["offset"] = round(offset)
        result["mark_suspect"] = abs(result["offset"]) > FC_MARK_TOLERANCE
        if abs(offset) <= 5:
            where = "right at your mark"
        elif offset < 0:
            where = f"{abs(round(offset))}s before your mark"
        else:
            where = f"{round(offset)}s after your mark"
        result["details"] = (
            f"curve shows the FC crash at {_fmt_time(detected_time)} "
            f"({result['detected_bt']:.0f}F, RoR {best_drop:+.1f} F/min), {where}"
        )
    else:
        result["details"] = (
            f"FC not marked; curve shows the FC crash at {_fmt_time(detected_time)} "
            f"({result['detected_bt']:.0f}F, RoR {best_drop:+.1f} F/min)"
        )
    return result


def extract_metrics(data):
    """Extract all relevant metrics from roast data.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().

    Returns:
        Dict with all calculated metrics.
    """
    computed = data.get("computed", {})
    phases = get_phase_percentages(computed)

    # Compute heat adjustments first — needed by assess_ror_smoothness()
    heat_adj_count = count_heat_adjustments(data)

    metrics = {
        # Phase percentages
        "dry_phase_pct": phases.get("dry_phase_pct", 0),
        "mid_phase_pct": phases.get("mid_phase_pct", 0),
        "dev_phase_pct": phases.get("dev_phase_pct", 0),

        # Phase times (seconds)
        "dry_phase_time": phases.get("dry_phase_time", 0),
        "mid_phase_time": phases.get("mid_phase_time", 0),
        "dev_phase_time": phases.get("dev_phase_time", 0),
        "total_time": phases.get("total_time", 0),

        # Key temperatures
        "charge_bt": computed.get("CHARGE_BT", 0),
        "charge_et": computed.get("CHARGE_ET", 0),
        "tp_bt": computed.get("TP_BT", 0),
        "tp_time": computed.get("TP_time", 0),
        "dry_bt": computed.get("DRY_BT", 0),
        "fc_bt": computed.get("FCs_BT", 0),
        "fc_time": computed.get("FCs_time", 0),
        "drop_bt": computed.get("DROP_BT", 0),
        "drop_time": computed.get("DROP_time", 0),
        "met": computed.get("MET", 0),  # Max ET

        # Rate of Rise
        "ror_at_fc": computed.get("fcs_ror", 0),
        "dry_phase_ror": computed.get("dry_phase_ror", 0),
        "mid_phase_ror": computed.get("mid_phase_ror", 0),
        "dev_phase_ror": computed.get("finish_phase_ror", 0),
        "total_ror": computed.get("total_ror", 0),

        # Temperature deltas
        "dry_delta_temp": computed.get("dry_phase_delta_temp", 0),
        "mid_delta_temp": computed.get("mid_phase_delta_temp", 0),
        "dev_delta_temp": computed.get("finish_phase_delta_temp", 0),

        # Heat adjustments (computed first — used by RoR smoothness)
        "heat_adjustments": heat_adj_count,

        # RoR smoothness (phase-segmented, with heat correlation)
        "ror_smoothness": assess_ror_smoothness(data, heat_adj_count),

        # Curve-detected first crack vs the operator's mark (None if the
        # curve never reached the FC band)
        "fc_check": detect_fc_from_curve(data),

        # Energy
        "auc": computed.get("AUC", 0),

        # Weight — Artisan reports weight_loss as 100% when weight-out was
        # never entered, which is garbage; zero it out unless out > 0
        "weight_in": computed.get("weightin", 0),
        "weight_out": computed.get("weightout", 0),
        "weight_loss_pct": computed.get("weight_loss", 0) if computed.get("weightout", 0) else 0,
    }

    return metrics


def validate_metrics(metrics):
    """Check metrics for suspicious or missing data.

    Returns a list of warning strings. Empty list means data looks OK.

    Args:
        metrics: Dict from extract_metrics().

    Returns:
        List of warning message strings.
    """
    warnings = []

    # Missing charge temperature — CHARGE event not recorded properly
    # (Artisan uses 0 or -1 for "not recorded")
    if metrics.get("charge_bt", 0) <= 0:
        warnings.append("CHARGE BT missing — CHARGE event may not have been recorded")
    if metrics.get("charge_et", 0) <= 0:
        warnings.append("CHARGE ET missing — CHARGE event may not have been recorded")

    # Drying phase way out of range (normal is ~45-57% under hot charge,
    # with Artisan auto-marking DRY at 300F BT)
    dry_pct = metrics.get("dry_phase_pct", 0)
    if dry_pct > 60:
        warnings.append(f"Drying phase {dry_pct}% is abnormally high — possible CHARGE timing issue")
    elif 0 < dry_pct < 35:
        warnings.append(f"Drying phase {dry_pct}% is abnormally low")

    # FC never marked — phase metrics and dev time can't be computed
    if metrics.get("drop_bt", 0) > 0 and metrics.get("fc_bt", 0) <= 0:
        warnings.append("FC event missing — mark FCs in Artisan; phase and dev-time metrics unavailable")

    # Development phase sanity (normal is ~10-20%)
    dev_pct = metrics.get("dev_phase_pct", 0)
    if dev_pct > 25:
        warnings.append(f"Development phase {dev_pct}% is abnormally high")
    elif metrics.get("total_time", 0) > 0 and dev_pct == 0:
        warnings.append("Development phase is 0% — FC or DROP event may be missing")

    # Total time sanity (normal is ~9-15 min)
    total = metrics.get("total_time", 0)
    if total > 0 and total < 300:
        warnings.append(f"Total time {total:.0f}s ({total/60:.1f}min) is unusually short")
    elif total > 1080:
        warnings.append(f"Total time {total:.0f}s ({total/60:.1f}min) is unusually long")

    # FC temp sanity (normal is ~350-400F)
    fc_bt = metrics.get("fc_bt", 0)
    if fc_bt > 0 and (fc_bt < 330 or fc_bt > 420):
        warnings.append(f"FC temp {fc_bt}F is outside expected range (330-420F)")

    # Drop temp should be >= FC temp
    drop_bt = metrics.get("drop_bt", 0)
    if drop_bt > 0 and fc_bt > 0 and drop_bt < fc_bt - 5:
        warnings.append(f"Drop temp {drop_bt}F is below FC temp {fc_bt}F — possible event recording error")

    # Turning point should be well below FC
    tp_bt = metrics.get("tp_bt", 0)
    if tp_bt > 0 and tp_bt > 200:
        warnings.append(f"Turning point {tp_bt}F is unusually high")

    return warnings


def add_audio_metrics(metrics, audio_data):
    """Attach the ear's first-crack verdict (crack_loader.extract_audio_data)
    as metrics["fc_audio"], parallel to the curve-based fc_check.

    Args:
        metrics: Metrics dict from extract_metrics().
        audio_data: Flat fc_audio dict, or None.

    Returns:
        The metrics dict (modified in place).
    """
    if audio_data:
        metrics["fc_audio"] = audio_data
    return metrics


def add_visual_metrics(metrics, visual_data):
    """Merge sentinel visual data into the metrics dict.

    Args:
        metrics: Existing metrics dict from extract_metrics().
        visual_data: Visual data dict from sentinel_loader.extract_visual_data().

    Returns:
        Updated metrics dict with visual fields added.
    """
    if not visual_data:
        return metrics

    metrics["visual_source"] = visual_data.get("visual_source", "Sentinel")
    metrics["visual_development_scores"] = visual_data.get("trajectory", [])
    metrics["visual_final_score"] = visual_data.get("final_score", 0)
    metrics["visual_uniformity"] = visual_data.get("uniformity_rating", "unknown")
    metrics["visual_score_count"] = visual_data.get("score_count", 0)
    metrics["visual_final_color"] = visual_data.get("final_color", "")

    return metrics


def _fmt_time(seconds):
    """Format seconds as M:SS."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"
