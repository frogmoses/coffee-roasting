"""Recommendation engine for coffee roast analysis.

Generates prioritized, actionable recommendations based on
roast metrics and optional bean profile data from find-coffee.
"""

from sentinel_loader import detect_plateau
from roast_metrics import (
    TARGETS,
    SAFETY_EJECT_BT,
    extract_metrics,
    add_visual_metrics,
    compare_to_targets,
    validate_metrics,
    _fmt_time,
)


def _target_str(key):
    """Human-readable target range for a metric, derived from TARGETS.

    Keeps recommendation text in sync with the targets dict (including
    any targets.json overrides) instead of hardcoding numbers.
    """
    t = TARGETS[key]
    if "target" in t:
        if key == "total_time":
            return f"~{_fmt_time(t['target'])}"
        return f"~{t['target']:g}{t['unit']}"
    if "min" in t and "max" in t:
        if t["unit"] == "s":
            return f"{_fmt_time(t['min'])}-{_fmt_time(t['max'])}"
        return f"{t['min']:g}-{t['max']:g}{t['unit']}"
    return f"max {t['max']:g}"


def _target_ideal(key):
    """Single ideal value for a metric (range midpoint for min/max targets)."""
    t = TARGETS[key]
    if "target" in t:
        return t["target"]
    if "min" in t and "max" in t:
        return (t["min"] + t["max"]) / 2
    return t["max"]


def analyze_roast(data, bean_profile=None, visual_data=None):
    """Full analysis of a roast: metrics, comparison, recommendations.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().
        bean_profile: Optional bean profile from coffee_lookup.extract_bean_profile().
        visual_data: Optional visual data from sentinel_loader.extract_visual_data().

    Returns:
        Dict with metrics, comparisons, recommendations, and metadata.
    """
    metrics = extract_metrics(data)

    # Merge sentinel visual data if available
    if visual_data:
        metrics = add_visual_metrics(metrics, visual_data)

    # Validate metrics for suspicious or missing data
    data_warnings = validate_metrics(metrics)

    comparisons = compare_to_targets(metrics)
    recommendations = generate_recommendations(comparisons, metrics, bean_profile)

    return {
        "roast_id": data.get("roast_id", ""),
        "title": data.get("title", ""),
        "roast_date": data.get("roast_date", ""),
        "batch_nr": data.get("batch_nr", 0),
        "cupping_notes": data.get("cupping_notes", ""),
        "roasting_notes": data.get("roasting_notes", ""),
        "metrics": metrics,
        "comparisons": comparisons,
        "recommendations": recommendations,
        "bean_profile": bean_profile,
        "warnings": data_warnings,
    }


def generate_recommendations(comparisons, metrics, bean_profile=None):
    """Produce prioritized roast improvement advice.

    Three categories:
    1. Roast mechanics (phase timing, heat control, RoR)
    2. Bean-specific advice (based on flavor profile)
    3. Flavor gap analysis (professional vs actual cupping notes)

    Args:
        comparisons: From compare_to_targets().
        metrics: From extract_metrics().
        bean_profile: Optional bean profile dict.

    Returns:
        List of recommendation dicts with priority, category, and text.
    """
    recs = []

    # 1. Roast mechanics recommendations
    recs.extend(_mechanic_recommendations(comparisons, metrics))

    # 2. Bean-specific recommendations
    if bean_profile:
        recs.extend(_bean_recommendations(bean_profile, metrics))

    # 3. Flavor gap analysis
    if bean_profile:
        recs.extend(_flavor_gap_recommendations(bean_profile, metrics))

    # 4. Visual-based recommendations (from sentinel camera)
    recs.extend(_visual_recommendations(metrics))

    # Sort by priority (1=highest)
    recs.sort(key=lambda r: r["priority"])
    return recs


def _mechanic_recommendations(comparisons, metrics):
    """Generate recommendations based on roast mechanics.

    Uses root cause grouping to combine related off-target metrics into
    single actionable recommendations, then handles remaining metrics
    individually.
    """
    recs = []

    # Build lookup of off-target comparisons
    off_target = {}
    comp_lookup = {}
    for comp in comparisons:
        comp_lookup[comp["metric"]] = comp
        if comp["status"] != "OK":
            off_target[comp["metric"]] = comp

    # Root cause grouping — combine related off-target metrics
    handled = set()

    # Charge too cold: low TP + long drying
    tp_comp = off_target.get("tp_bt")
    dry_comp = off_target.get("dry_phase_pct")
    if (tp_comp and "LOW" in tp_comp["status"]
            and dry_comp and "HIGH" in dry_comp["status"]):
        recs.append({
            "priority": 1,
            "category": "Charge Temp",
            "text": (
                f"Charge temp too low (TP {tp_comp['actual']}F), stretched drying "
                f"to {dry_comp['actual']}%. Preheat more to compress drying and "
                f"free up Maillard time."
            ),
        })
        handled.update(("tp_bt", "dry_phase_pct"))

    # Insufficient momentum: low RoR at FC + low FC temp
    ror_comp = off_target.get("ror_at_fc")
    fc_comp = off_target.get("fc_bt")
    if (ror_comp and "LOW" in ror_comp["status"]
            and fc_comp and "LOW" in fc_comp["status"]):
        recs.append({
            "priority": 1,
            "category": "RoR Control",
            "text": (
                f"Not enough heat into FC (RoR {ror_comp['actual']} F/min, FC at "
                f"{fc_comp['actual']}F). Maintain steady heat through Maillard."
            ),
        })
        handled.update(("ror_at_fc", "fc_bt"))

    # Too much momentum: high RoR at FC + high drop or FC temp
    drop_comp = off_target.get("drop_bt")
    if (ror_comp and "HIGH" in ror_comp["status"]
            and ((drop_comp and "HIGH" in drop_comp["status"])
                 or (fc_comp and "HIGH" in fc_comp["status"]))):
        recs.append({
            "priority": 1,
            "category": "RoR Control",
            "text": (
                f"Too much energy into/through FC (RoR {ror_comp['actual']} F/min). "
                f"Cut heat earlier (around 340F, per the Hottop manual) and "
                f"shorten the time after FC."
            ),
        })
        handled.add("ror_at_fc")
        if drop_comp and "HIGH" in drop_comp["status"]:
            handled.add("drop_bt")
        if fc_comp and "HIGH" in fc_comp["status"]:
            handled.add("fc_bt")

    # Overdevelopment: high dev % + high drop temp. Drop temp is an outcome
    # of dev time under the FC-timed regime, so the fix is stated in seconds.
    dev_comp = off_target.get("dev_phase_pct")
    devt_comp = off_target.get("dev_phase_time")
    if (dev_comp and "HIGH" in dev_comp["status"]
            and drop_comp and "HIGH" in drop_comp["status"]):
        dev_secs = metrics.get("dev_phase_time", 0)
        recs.append({
            "priority": 1,
            "category": "Phase Timing",
            "text": (
                f"Development ran long ({dev_comp['actual']}%, "
                f"{_fmt_time(dev_secs)} after FC) and drop came in high "
                f"({drop_comp['actual']}F). Shorten time after FC by ~15s "
                f"to preserve origin character."
            ),
        })
        handled.update(("dev_phase_pct", "drop_bt", "dev_phase_time"))

    # Development length: time-after-FC and DTR agree on direction — one rec
    # keyed on the time lever (the percentage is the diagnostic view)
    if (devt_comp and dev_comp
            and "dev_phase_pct" not in handled
            and devt_comp["status"] == dev_comp["status"]):
        if "HIGH" in devt_comp["status"]:
            fix = "Shorten the time after FC by ~15s."
        else:
            fix = "Extend the time after FC by ~15s."
        recs.append({
            "priority": 2,
            "category": "Phase Timing",
            "text": (
                f"Development was {_fmt_time(devt_comp['actual'])} after FC "
                f"({dev_comp['actual']}% DTR; target "
                f"{_target_str('dev_phase_time')} / {_target_str('dev_phase_pct')}). "
                f"{fix}"
            ),
        })
        handled.update(("dev_phase_time", "dev_phase_pct"))

    # Individual metric recommendations for anything not grouped
    for comp in comparisons:
        if comp["status"] == "OK":
            continue

        key = comp["metric"]
        if key in handled:
            continue

        actual = comp["actual"]
        status = comp["status"]

        if key == "dry_phase_pct":
            if "HIGH" in status:
                recs.append({
                    "priority": 1,
                    "category": "Phase Timing",
                    "text": (
                        f"Drying phase too long at {actual}% (target {_target_str('dry_phase_pct')}). "
                        f"Increase charge temperature or early heat to compress drying. "
                        f"This robs time from Maillard where sweetness develops."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Drying phase too short at {actual}% (target {_target_str('dry_phase_pct')}). "
                        f"Lower charge temperature or initial heat to allow proper drying."
                    ),
                })

        elif key == "mid_phase_pct":
            if "LOW" in status:
                recs.append({
                    "priority": 1,
                    "category": "Phase Timing",
                    "text": (
                        f"Maillard phase too short at {actual}% (target {_target_str('mid_phase_pct')}). "
                        f"This means less time for sweetness and body to develop. "
                        f"Compress drying to free up time for Maillard."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Maillard phase long at {actual}% (target {_target_str('mid_phase_pct')}). "
                        f"Risk of baked flavors if RoR is too flat."
                    ),
                })

        elif key == "dev_phase_pct":
            if "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Development at {actual}% is long (target {_target_str('dev_phase_pct')}). "
                        f"Risk of overdevelopment and losing origin character."
                    ),
                })
            elif "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Development at {actual}% is short (target {_target_str('dev_phase_pct')}). "
                        f"May result in grassy or underdeveloped flavors."
                    ),
                })

        elif key == "dev_phase_time":
            # The direct lever: seconds from FC to drop
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Only {_fmt_time(actual)} after FC before drop "
                        f"(target {_target_str('dev_phase_time')}). "
                        f"Run 15-20 seconds longer after first crack."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"{_fmt_time(actual)} after FC before drop is long "
                        f"(target {_target_str('dev_phase_time')}). "
                        f"Shorten the time after first crack by ~15s."
                    ),
                })

        elif key == "total_time":
            target_time = _fmt_time(TARGETS["total_time"]["target"])
            if "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Roast Length",
                    "text": (
                        f"Total roast time {_fmt_time(actual)} is long (target ~{target_time}). "
                        f"Risk of baked flavors. Increase charge temp or initial heat."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Roast Length",
                    "text": (
                        f"Total roast time {_fmt_time(actual)} is short (target ~{target_time}). "
                        f"May need to slow momentum earlier."
                    ),
                })

        elif key == "heat_adjustments":
            if "HIGH" in status:
                recs.append({
                    "priority": 1,
                    "category": "Heat Control",
                    "text": (
                        f"Too many heat adjustments ({actual}, max 4). "
                        f"Each adjustment causes RoR oscillation which leads to uneven "
                        f"roasting and surface scorching. Plan 3-4 deliberate cuts instead."
                    ),
                })

        elif key == "ror_at_fc":
            ror_target = _target_str("ror_at_fc")
            if "HIGH" in status:
                recs.append({
                    "priority": 1,
                    "category": "RoR Control",
                    "text": (
                        f"RoR at first crack is {actual} F/min (target {ror_target}). "
                        f"Too much momentum going into crack. Start cutting heat "
                        f"earlier (around 340F) for a gentler approach."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "RoR Control",
                    "text": (
                        f"RoR at first crack is {actual} F/min (target {ror_target}). "
                        f"Low RoR may indicate stalling. Maintain steady heat through mid-roast."
                    ),
                })

        elif key == "tp_bt":
            tp_target = _target_str("tp_bt")
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Charge Temp",
                    "text": (
                        f"Turning point at {actual}F is low (target {tp_target}). "
                        f"Preheat more aggressively for a higher charge temperature. "
                        f"This will help compress the drying phase."
                    ),
                })
            elif "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Charge Temp",
                    "text": (
                        f"Turning point at {actual}F is high (target {tp_target}). "
                        f"Charge was hotter than usual — if drying compresses too "
                        f"much, charge slightly cooler or trim early heat."
                    ),
                })

        elif key == "drop_bt":
            # Drop temp is an outcome of dev time under the FC-timed regime —
            # frame the fix in seconds after FC, not as a temp to aim at
            drop_target = _target_str("drop_bt")
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"Drop temp {actual}F came in low (typical {drop_target}), "
                        f"which usually means development was short or momentum "
                        f"faded. Run longer after first crack, or carry more "
                        f"heat into crack."
                    ),
                })
            elif "HIGH" in status:
                safety_note = ""
                if actual >= SAFETY_EJECT_BT - 5:
                    safety_note = (
                        f" Note: the Hottop safety-ejects at {SAFETY_EJECT_BT}F BT."
                    )
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"Drop temp {actual}F came in high (typical {drop_target}), "
                        f"which means development ran long or hot. Shorten the "
                        f"time after first crack or cut heat earlier.{safety_note}"
                    ),
                })

        elif key == "fc_bt":
            fc_target = _target_str("fc_bt")
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"First crack at {actual}F is below target ({fc_target}). "
                        f"The beans may not be fully developed. Ensure steady "
                        f"heat through the Maillard phase — avoid cutting heat "
                        f"too early."
                    ),
                })
            elif "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"First crack at {actual}F is above target ({fc_target}). "
                        f"Too much energy going into crack. Reduce heat earlier "
                        f"in the Maillard phase (around 340F)."
                    ),
                })

    # First-crack RoR defects (Rao/Cropster): crash bakes, flick chars.
    # These outrank generic oscillation advice because they map directly
    # to known flavor faults (e.g. smoky/ashy notes from a flick).
    ror_info = metrics.get("ror_smoothness", {})
    if ror_info.get("fc_flick"):
        crash_part = ""
        if ror_info.get("fc_crash"):
            crash_part = (
                f" after crashing to {ror_info.get('crash_min_ror')} F/min"
            )
        recs.append({
            "priority": 1,
            "category": "RoR Control",
            "text": (
                f"RoR flicked back upward after first crack{crash_part}. "
                f"The flick chars delicate beans (smoky/ashy notes). Never add "
                f"heat during first crack — plan one deliberate cut around "
                f"340-345F and hold through the crack."
            ),
        })
    elif ror_info.get("fc_crash"):
        recs.append({
            "priority": 2,
            "category": "RoR Control",
            "text": (
                f"RoR crashed to {ror_info.get('crash_min_ror')} F/min right "
                f"after first crack. A crash bakes the coffee flat. Carry a "
                f"little more momentum into FC and make the pre-FC heat cut "
                f"smaller or earlier — don't add heat back during the crack."
            ),
        })

    # Context-aware RoR smoothness check
    heat_corr = ror_info.get("heat_correlation", "unknown")
    severity = ror_info.get("severity")
    osc_count = ror_info.get("oscillations", 0)

    if severity in ("oscillating", "moderate"):
        if heat_corr == "low_input":
            # Few heat changes — oscillation is natural thermal behavior, not user error
            recs.append({
                "priority": 3,
                "category": "RoR Control",
                "text": (
                    f"RoR has {osc_count} direction changes despite few heat adjustments "
                    f"({metrics.get('heat_adjustments', 0)}). This is likely natural thermal "
                    f"behavior rather than control error. Try holding heat steady longer "
                    f"before each cut, or cut slightly earlier before FC to smooth the curve."
                ),
            })
        elif heat_corr == "high_input":
            # Many heat changes — original strong warning
            if severity == "oscillating":
                recs.append({
                    "priority": 1,
                    "category": "RoR Control",
                    "text": (
                        f"RoR curve is oscillating ({osc_count} direction changes). "
                        f"This causes uneven heat transfer and harsh/smoky notes. "
                        f"Reduce heat adjustment frequency and magnitude."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "RoR Control",
                    "text": (
                        f"RoR has moderate oscillation ({osc_count} direction changes). "
                        f"Aim for a smooth declining curve with fewer, smaller adjustments."
                    ),
                })
        else:
            # Unknown correlation — generic advice
            if severity == "oscillating":
                recs.append({
                    "priority": 2,
                    "category": "RoR Control",
                    "text": (
                        f"RoR curve is oscillating ({osc_count} direction changes). "
                        f"Aim for a smooth declining curve through Maillard and development."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "RoR Control",
                    "text": (
                        f"RoR has moderate oscillation ({osc_count} direction changes). "
                        f"Aim for a smooth declining curve with fewer, smaller adjustments."
                    ),
                })

    # Post-pass: link RoR oscillation and low FC RoR recs if both present
    has_oscillation = any(
        r["category"] == "RoR Control" and "oscillat" in r["text"].lower()
        for r in recs
    )
    if has_oscillation:
        for r in recs:
            if r["category"] == "RoR Control" and "Low RoR may indicate stalling" in r["text"]:
                r["text"] += (
                    " This is likely related to the RoR oscillation above — "
                    "unsteady heat wastes energy instead of building momentum."
                )
                break

    return recs


def _bean_recommendations(bean_profile, metrics):
    """Generate recommendations based on the bean's flavor profile."""
    recs = []
    flavors = bean_profile.get("flavor_scores", {})
    cupping = bean_profile.get("cupping_scores", {})

    # High fruit/berry/citrus: protect origin character
    fruit_total = flavors.get("berry", 0) + flavors.get("fruit", 0) + flavors.get("citrus", 0)
    if fruit_total >= 15:
        recs.append({
            "priority": 2,
            "category": "Bean Profile",
            "text": (
                f"High fruit character (berry={flavors.get('berry', 0)}, "
                f"fruit={flavors.get('fruit', 0)}, citrus={flavors.get('citrus', 0)}). "
                f"Protect these with shorter development (~15% DTR) and "
                f"drop temp 375-380F. Avoid overdevelopment."
            ),
        })
    elif fruit_total >= 10:
        recs.append({
            "priority": 3,
            "category": "Bean Profile",
            "text": (
                f"Moderate fruit character present. Keep development "
                f"around 15% to preserve fruit notes."
            ),
        })

    # High body/cocoa: can handle more development
    if flavors.get("body", 0) >= 7 or flavors.get("cocoa", 0) >= 7:
        recs.append({
            "priority": 3,
            "category": "Bean Profile",
            "text": (
                f"High body ({flavors.get('body', 0)}) and cocoa ({flavors.get('cocoa', 0)}) scores. "
                f"This coffee can handle more development for a fuller, "
                f"more chocolatey cup if desired."
            ),
        })

    # Clean cup score informs precision needs
    clean_cup = cupping.get("clean_cup", 0)
    if clean_cup >= 4.0:
        recs.append({
            "priority": 3,
            "category": "Bean Profile",
            "text": (
                f"High clean cup score ({clean_cup}). This coffee is forgiving — "
                f"small roasting errors are less likely to show up as defects."
            ),
        })
    elif clean_cup > 0 and clean_cup < 3.0:
        recs.append({
            "priority": 2,
            "category": "Bean Profile",
            "text": (
                f"Lower clean cup score ({clean_cup}). Precision matters more — "
                f"roasting defects will show through. Focus on smooth RoR."
            ),
        })

    # Floral character: very delicate
    if flavors.get("floral", 0) >= 6:
        recs.append({
            "priority": 2,
            "category": "Bean Profile",
            "text": (
                f"Strong floral notes (score={flavors.get('floral', 0)}). "
                f"These are very heat-sensitive. Keep total roast under 12 min "
                f"and development under 15%."
            ),
        })

    return recs


def _flavor_gap_recommendations(bean_profile, metrics):
    """Compare professional cupping notes against actual results.

    Identifies what roasting defects might be masking the expected flavors.
    """
    recs = []
    pro_notes = bean_profile.get("cupping_notes", "").lower()
    dominant = bean_profile.get("dominant_flavors", [])

    if not pro_notes and not dominant:
        return recs

    # Build a summary of what the coffee should taste like
    if dominant:
        flavor_str = ", ".join(f"{name} ({score})" for name, score in dominant)
        notes = bean_profile.get("cupping_notes", "N/A")
        # Truncate to first 2 sentences for default display
        sentences = notes.split(". ")
        if len(sentences) > 2:
            short_notes = ". ".join(sentences[:2]) + "."
        else:
            short_notes = notes
        full_text = f"Target flavor profile: {flavor_str}. Professional notes: \"{notes}\""
        recs.append({
            "priority": 3,
            "category": "Flavor Goal",
            "text": (
                f"Target flavor profile: {flavor_str}. "
                f"Professional notes: \"{short_notes}\""
            ),
            "full_text": full_text,
        })

    return recs


def _visual_recommendations(metrics):
    """Generate recommendations based on sentinel visual data.

    Analyzes the development score trajectory and uniformity to detect
    issues like score plateaus (stalling) and uneven development.
    If BT data has been enriched into trajectory points, it is included
    in recommendation text for actionable context.
    """
    recs = []
    trajectory = metrics.get("visual_development_scores", [])
    uniformity = metrics.get("visual_uniformity", "unknown")

    if not trajectory or len(trajectory) < 3:
        return recs

    # Check for score plateau — consecutive readings with same score
    # during maillard or development phases (indicates stalling).
    # Shared detector keeps this in sync with the summary line.
    plateau = detect_plateau(trajectory)
    if plateau:
        # BT at the start of the plateau for context
        plateau_bt_str = ""
        start_pt = trajectory[plateau["start_index"]]
        if start_pt.get("bt"):
            plateau_bt_str = f" (BT was around {start_pt['bt']}F)"
        recs.append({
            "priority": 2,
            "category": "Visual Dev",
            "text": (
                f"Visual development stalled at score {plateau['score']}/10 for "
                f"{plateau['run']} consecutive readings{plateau_bt_str}. This may indicate "
                f"insufficient heat during a critical phase. Consider maintaining "
                f"or increasing heat input earlier."
            ),
        })

    # Check for rapid score jumps (too aggressive heat)
    for i in range(1, len(trajectory)):
        prev = trajectory[i - 1]
        curr = trajectory[i]
        jump = curr["score"] - prev["score"]
        if jump >= 3:
            # Include BT at the jump point if available
            bt_str = f" at {curr['bt']}F BT" if curr.get("bt") else ""
            recs.append({
                "priority": 2,
                "category": "Visual Dev",
                "text": (
                    f"Rapid visual development jump ({prev['score']} to {curr['score']}) "
                    f"at {_fmt_time(curr['elapsed'])}{bt_str}. This suggests heat was too "
                    f"aggressive at that point. Reduce heat earlier for smoother progression."
                ),
            })
            break  # Only report the first big jump

    # Uniformity assessment
    if uniformity == "poor":
        recs.append({
            "priority": 1,
            "category": "Visual Dev",
            "text": (
                "Poor visual uniformity detected across the batch. This indicates "
                "uneven heat distribution — check drum charge amount, preheat "
                "evenness, and drum rotation speed."
            ),
        })
    elif uniformity == "moderate":
        recs.append({
            "priority": 3,
            "category": "Visual Dev",
            "text": (
                "Moderate visual uniformity — some variation in bean color. "
                "Consider slightly reducing batch size or adjusting charge "
                "temperature for more even development."
            ),
        })

    # Final score assessment for light roasts
    final_score = metrics.get("visual_final_score", 0)
    dev_pct = metrics.get("dev_phase_pct", 0)
    if final_score > 0 and final_score >= 8 and dev_pct < 14:
        recs.append({
            "priority": 2,
            "category": "Visual Dev",
            "text": (
                f"High visual score ({final_score}/10) with short development "
                f"({dev_pct}%). The beans look darker than the development time "
                f"suggests — possible surface scorching. Try lower heat through "
                f"development for more even internal/external roast."
            ),
        })

    return recs


def generate_next_roast_summary(comparisons, metrics, recommendations):
    """Distill top recs into 2-4 concrete action items for the next roast.

    Scans off-target comparisons and priority recs, maps each to a concrete
    adjustment, deduplicates, and caps at 4 items.

    Args:
        comparisons: From compare_to_targets().
        metrics: From extract_metrics().
        recommendations: From generate_recommendations().

    Returns:
        List of action strings (max 4).
    """
    actions = []
    seen = set()  # track action keys to avoid duplicates

    # Build lookup of what's off-target
    off_target = {c["metric"]: c["status"] for c in comparisons if c["status"] != "OK"}

    # Build lookup of rec categories/texts for pattern matching
    rec_texts = " ".join(r["text"].lower() for r in recommendations)
    high_pri_cats = {r["category"] for r in recommendations if r["priority"] == 1}

    # Map patterns to concrete actions
    # Long drying / low FC RoR / low TP → charge hotter
    if ("dry_phase_pct" in off_target and "HIGH" in off_target["dry_phase_pct"]) or \
       ("tp_bt" in off_target and "LOW" in off_target["tp_bt"]):
        actions.append("Charge hotter — aim for a turning point around 145-150F to compress drying")
        seen.add("charge")

    # FC crash/flick → one planned cut, held through the crack
    ror_info = metrics.get("ror_smoothness", {})
    if ror_info.get("fc_flick") or ror_info.get("fc_crash"):
        actions.append("Plan one heat cut around 340-345F and hold it through first crack — no adjustments during the crack")
        seen.add("heat_cuts")

    # RoR oscillating or too many heat changes → advice depends on heat correlation
    heat_corr = ror_info.get("heat_correlation", "unknown")
    if ("Heat Control" in high_pri_cats or "oscillat" in rec_texts) and "heat_cuts" not in seen:
        if heat_corr == "low_input":
            # User already makes few cuts — different advice
            actions.append("Hold heat steady longer between cuts — the curve will smooth naturally")
        else:
            actions.append("Plan 2-3 deliberate heat cuts instead of frequent small adjustments")
        seen.add("heat_cuts")

    # Short development (time after FC or low drop temp) → run longer
    short_dev = (
        ("dev_phase_time" in off_target and "LOW" in off_target["dev_phase_time"])
        or ("drop_bt" in off_target and "LOW" in off_target["drop_bt"])
    )
    if short_dev and "dev_time" not in seen:
        actions.append("Run 15-20 seconds longer after first crack before dropping")
        seen.add("dev_time")

    # Low FC temp → maintain heat through Maillard
    if "fc_bt" in off_target and "LOW" in off_target["fc_bt"] and "maillard" not in seen:
        actions.append("Maintain steady heat through Maillard — avoid cutting heat before 340F")
        seen.add("maillard")

    # High FC RoR → cut heat earlier
    if "ror_at_fc" in off_target and "HIGH" in off_target["ror_at_fc"] and "heat_cuts" not in seen:
        actions.append("Start cutting heat earlier, around 340F, for a gentler approach to first crack")
        seen.add("heat_cuts")

    # Low FC RoR → more momentum
    if "ror_at_fc" in off_target and "LOW" in off_target["ror_at_fc"] and "charge" not in seen:
        actions.append("Carry more heat momentum into first crack — avoid early heat cuts")
        seen.add("charge")

    # Long development (time after FC or high drop temp) → shorten
    long_dev = (
        ("dev_phase_time" in off_target and "HIGH" in off_target["dev_phase_time"])
        or ("drop_bt" in off_target and "HIGH" in off_target["drop_bt"])
    )
    if long_dev and "dev_time" not in seen:
        actions.append("Shorten the time after first crack by ~15 seconds to preserve origin character")
        seen.add("dev_time")

    # Visual: poor uniformity → batch size or preheat
    uniformity = metrics.get("visual_uniformity", "unknown")
    if uniformity == "poor" and "uniformity" not in seen:
        actions.append("Reduce batch size or preheat longer for more even development")
        seen.add("uniformity")

    # Visual: stalled development → maintain heat
    if "stalled" in rec_texts and "visual_stall" not in seen:
        actions.append("Maintain heat through mid-roast \u2014 visual development stalled")
        seen.add("visual_stall")

    # Cap at 4
    return actions[:4]


def compare_roasts(analysis1, analysis2):
    """Side-by-side comparison of two roasts.

    Args:
        analysis1: First roast analysis dict.
        analysis2: Second roast analysis dict.

    Returns:
        List of comparison dicts showing improvements/regressions.
    """
    m1 = analysis1.get("metrics", {})
    m2 = analysis2.get("metrics", {})

    # Keys to compare; ideals derive from TARGETS so they can't drift
    # from the active target definitions (including targets.json overrides)
    compare_keys = [
        ("dry_phase_pct", "Drying %"),
        ("mid_phase_pct", "Maillard %"),
        ("dev_phase_pct", "Development %"),
        ("dev_phase_time", "Dev time (s)"),
        ("total_time", "Total time (s)"),
        ("tp_bt", "Turning point"),
        ("fc_bt", "FC temp"),
        ("drop_bt", "Drop temp"),
        ("ror_at_fc", "RoR at FC"),
        ("heat_adjustments", "Heat changes"),
    ]

    changes = []
    for key, label in compare_keys:
        ideal = _target_ideal(key)
        v1 = m1.get(key, 0)
        v2 = m2.get(key, 0)
        delta = v2 - v1

        # Determine if change is improvement (closer to ideal)
        dist1 = abs(v1 - ideal)
        dist2 = abs(v2 - ideal)
        if dist2 < dist1:
            direction = "improved"
        elif dist2 > dist1:
            direction = "regressed"
        else:
            direction = "unchanged"

        changes.append({
            "metric": key,
            "label": label,
            "roast1": v1,
            "roast2": v2,
            "delta": delta,
            "direction": direction,
        })

    return changes


