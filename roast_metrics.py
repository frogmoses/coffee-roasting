"""Metric extraction, target definitions, and comparison for coffee roasts.

Compares actual roast metrics against ideal targets derived from
NEXT-ROAST-PLAN.md and standard roasting best practices.
"""

from statistics import mean

# Target definitions for a fruit-forward light-medium roast
TARGETS = {
    "dry_phase_pct": {"target": 45.0, "tolerance": 3.0, "unit": "%", "label": "Drying phase"},
    "mid_phase_pct": {"target": 40.0, "tolerance": 3.0, "unit": "%", "label": "Maillard phase"},
    "dev_phase_pct": {"target": 15.0, "tolerance": 2.0, "unit": "%", "label": "Development phase"},
    "total_time": {"target": 675, "tolerance": 30, "unit": "s", "label": "Total time"},
    "tp_bt": {"min": 140, "max": 150, "unit": "F", "label": "Turning point BT"},
    "fc_bt": {"min": 358, "max": 362, "unit": "F", "label": "First crack BT"},
    "drop_bt": {"min": 375, "max": 380, "unit": "F", "label": "Drop BT"},
    "ror_at_fc": {"min": 12, "max": 14, "unit": "F/min", "label": "RoR at FC"},
    "heat_adjustments": {"max": 4, "unit": "count", "label": "Heat adjustments"},
}


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
    """Check for oscillation in the Rate of Rise curve.

    Excludes drying phase from oscillation counting since TP recovery
    naturally causes direction changes that aren't meaningful oscillation.
    Falls back to full-window analysis if DRY event wasn't recorded.

    Args:
        data: Extracted roast data.
        heat_adjustment_count: Number of heater events (for correlation).

    Returns:
        Dict with smoothness assessment: oscillation count, severity,
        phase-segmented counts, heat correlation, and details.
    """
    bt = data.get("bt", [])
    timex = data.get("timex", [])
    timeindex = data.get("timeindex", [])

    if len(bt) < 10 or len(timeindex) < 7:
        return {"oscillations": 0, "severity": "unknown", "details": "Insufficient data"}

    charge_idx = timeindex[0]
    drop_idx = timeindex[6] if timeindex[6] > 0 else len(bt) - 1
    # Phase boundaries: DRY (index 1) and FCs (index 2)
    dry_idx = timeindex[1] if len(timeindex) > 1 else 0
    fc_idx = timeindex[2] if len(timeindex) > 2 else 0

    # Calculate RoR using 30-second window (~15 data points at 2s interval)
    window = 15

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

    def _calc_ror_segment(start_idx, end_idx):
        """Calculate RoR values for a data index range."""
        values = []
        for i in range(max(start_idx, charge_idx + window), min(end_idx + 1, len(bt))):
            if i >= len(bt) or (i - window) < 0:
                continue
            dt = timex[i] - timex[i - window]
            if dt > 0:
                ror = (bt[i] - bt[i - window]) / dt * 60  # F/min
                values.append(ror)
        return values

    # If DRY event was recorded, do phase-segmented analysis
    use_phases = dry_idx > 0

    if use_phases:
        # Maillard phase: DRY -> FCs (or DROP if FCs not recorded)
        maillard_end = fc_idx if fc_idx > 0 else drop_idx
        maillard_ror = _calc_ror_segment(dry_idx, maillard_end)
        maillard_osc = _count_direction_changes(maillard_ror) if len(maillard_ror) >= 3 else 0

        # Development phase: FCs -> DROP
        dev_osc = 0
        dev_ror = []
        if fc_idx > 0:
            dev_ror = _calc_ror_segment(fc_idx, drop_idx)
            dev_osc = _count_direction_changes(dev_ror) if len(dev_ror) >= 3 else 0

        direction_changes = maillard_osc + dev_osc
        all_ror = maillard_ror + dev_ror

        # Lower thresholds since drying is excluded
        if direction_changes <= 2:
            severity = "smooth"
        elif direction_changes <= 4:
            severity = "moderate"
        else:
            severity = "oscillating"
    else:
        # Fallback: full-window analysis (DRY not recorded)
        all_ror = _calc_ror_segment(charge_idx, drop_idx)
        direction_changes = _count_direction_changes(all_ror) if len(all_ror) >= 3 else 0
        maillard_osc = 0
        dev_osc = 0

        # Original thresholds for full-window
        if direction_changes <= 3:
            severity = "smooth"
        elif direction_changes <= 6:
            severity = "moderate"
        else:
            severity = "oscillating"

    if len(all_ror) < 5:
        return {"oscillations": 0, "severity": "unknown", "details": "Too few RoR points"}

    # Heat correlation: how many heat inputs vs oscillation output
    if heat_adjustment_count <= 3:
        heat_correlation = "low_input"
    elif heat_adjustment_count > 4:
        heat_correlation = "high_input"
    else:
        heat_correlation = "unknown"

    return {
        "oscillations": direction_changes,
        "maillard_oscillations": maillard_osc,
        "dev_oscillations": dev_osc,
        "severity": severity,
        "heat_correlation": heat_correlation,
        "ror_min": round(min(all_ror), 1),
        "ror_max": round(max(all_ror), 1),
        "ror_mean": round(mean(all_ror), 1),
        "details": f"{direction_changes} significant direction changes in RoR (post-drying)",
    }


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

        # Energy
        "auc": computed.get("AUC", 0),

        # Weight
        "weight_in": computed.get("weightin", 0),
        "weight_out": computed.get("weightout", 0),
        "weight_loss_pct": computed.get("weight_loss", 0),
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
    if metrics.get("charge_bt", 0) == 0:
        warnings.append("CHARGE BT missing — CHARGE event may not have been recorded")
    if metrics.get("charge_et", 0) == 0:
        warnings.append("CHARGE ET missing — CHARGE event may not have been recorded")

    # Drying phase way out of range (normal is ~40-50%)
    dry_pct = metrics.get("dry_phase_pct", 0)
    if dry_pct > 55:
        warnings.append(f"Drying phase {dry_pct}% is abnormally high — possible CHARGE timing issue")
    elif 0 < dry_pct < 30:
        warnings.append(f"Drying phase {dry_pct}% is abnormally low")

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


def compare_to_targets(metrics):
    """Compare extracted metrics against ideal targets.

    Args:
        metrics: Dict from extract_metrics().

    Returns:
        List of comparison dicts sorted by severity (worst first).
        Each dict has: metric, label, actual, target_str, status, deviation.
    """
    comparisons = []

    for key, target in TARGETS.items():
        actual = metrics.get(key, None)
        if actual is None:
            continue

        if "target" in target and "tolerance" in target:
            # Target with tolerance (e.g., phase percentages)
            t = target["target"]
            tol = target["tolerance"]
            deviation = actual - t

            if abs(deviation) <= tol:
                status = "OK"
            elif deviation > 0:
                status = "!! HIGH"
            else:
                status = "!! LOW"

            # Format target string
            if key == "total_time":
                target_str = f"{_fmt_time(t)} +/- {int(tol)}s"
                actual_display = _fmt_time(actual)
            else:
                target_str = f"{t}{target['unit']} +/- {tol}"
                actual_display = f"{actual}{target['unit']}"

            comparisons.append({
                "metric": key,
                "label": target["label"],
                "actual": actual,
                "actual_display": actual_display,
                "target_str": target_str,
                "status": status,
                "deviation": abs(deviation),
                "deviation_display": f"{deviation:+.1f}{target['unit']}",
            })

        elif "min" in target and "max" in target:
            # Range target (e.g., FC temperature)
            tmin = target["min"]
            tmax = target["max"]

            if tmin <= actual <= tmax:
                status = "OK"
                deviation = 0
            elif actual < tmin:
                status = "!! LOW"
                deviation = tmin - actual
            else:
                status = "!! HIGH"
                deviation = actual - tmax

            target_str = f"{tmin}-{tmax}{target['unit']}"
            actual_display = f"{actual}{target['unit']}"

            comparisons.append({
                "metric": key,
                "label": target["label"],
                "actual": actual,
                "actual_display": actual_display,
                "target_str": target_str,
                "status": status,
                "deviation": deviation,
                "deviation_display": f"{deviation:+.1f}{target['unit']}" if deviation else "on target",
            })

        elif "max" in target:
            # Hard max limit (e.g., heat adjustments)
            tmax = target["max"]
            deviation = max(0, actual - tmax)

            if actual <= tmax:
                status = "OK"
            else:
                status = "!! HIGH"

            target_str = f"max {tmax}"
            actual_display = str(actual)

            comparisons.append({
                "metric": key,
                "label": target["label"],
                "actual": actual,
                "actual_display": actual_display,
                "target_str": target_str,
                "status": status,
                "deviation": deviation,
                "deviation_display": f"+{deviation}" if deviation else "OK",
            })

    # Sort: problems first (by deviation), then OK items
    comparisons.sort(key=lambda c: (0 if c["status"] != "OK" else 1, -c["deviation"]))
    return comparisons


def _fmt_time(seconds):
    """Format seconds as M:SS."""
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}:{s:02d}"
