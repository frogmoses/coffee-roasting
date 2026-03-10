"""Recommendation engine for coffee roast analysis.

Generates prioritized, actionable recommendations based on
roast metrics and optional bean profile data from find-coffee.
"""

from roast_metrics import extract_metrics, add_visual_metrics, compare_to_targets, _fmt_time


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

    # Merge visual data from r1-eye sentinel if available
    if visual_data:
        metrics = add_visual_metrics(metrics, visual_data)

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

    # 4. Visual-based recommendations (from r1-eye sentinel)
    recs.extend(_visual_recommendations(metrics))

    # Sort by priority (1=highest)
    recs.sort(key=lambda r: r["priority"])
    return recs


def _mechanic_recommendations(comparisons, metrics):
    """Generate recommendations based on roast mechanics."""
    recs = []

    for comp in comparisons:
        if comp["status"] == "OK":
            continue

        key = comp["metric"]
        actual = comp["actual"]
        status = comp["status"]

        if key == "dry_phase_pct":
            if "HIGH" in status:
                recs.append({
                    "priority": 1,
                    "category": "Phase Timing",
                    "text": (
                        f"Drying phase too long at {actual}% (target ~45%). "
                        f"Increase charge temperature to compress drying. "
                        f"This robs time from Maillard where sweetness develops."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Drying phase too short at {actual}% (target ~45%). "
                        f"Lower charge temperature or initial heat to allow proper drying."
                    ),
                })

        elif key == "mid_phase_pct":
            if "LOW" in status:
                recs.append({
                    "priority": 1,
                    "category": "Phase Timing",
                    "text": (
                        f"Maillard phase too short at {actual}% (target ~40%). "
                        f"This means less time for sweetness and body to develop. "
                        f"Compress drying to free up time for Maillard."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Maillard phase long at {actual}% (target ~40%). "
                        f"Risk of baked flavors if RoR is too flat."
                    ),
                })

        elif key == "dev_phase_pct":
            if "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Development at {actual}% is long (target ~15%). "
                        f"Risk of overdevelopment and losing origin character."
                    ),
                })
            elif "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Phase Timing",
                    "text": (
                        f"Development at {actual}% is short (target ~15%). "
                        f"May result in grassy or underdeveloped flavors."
                    ),
                })

        elif key == "total_time":
            if "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Roast Length",
                    "text": (
                        f"Total roast time {_fmt_time(actual)} is long (target ~{_fmt_time(675)}). "
                        f"Risk of baked flavors. Increase charge temp or initial heat."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "Roast Length",
                    "text": (
                        f"Total roast time {_fmt_time(actual)} is short (target ~{_fmt_time(675)}). "
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
            if "HIGH" in status:
                recs.append({
                    "priority": 1,
                    "category": "RoR Control",
                    "text": (
                        f"RoR at first crack is {actual} F/min (target 12-14). "
                        f"Too much momentum going into crack. Start cutting heat "
                        f"earlier (around 330-340F) for a gentler approach."
                    ),
                })
            else:
                recs.append({
                    "priority": 2,
                    "category": "RoR Control",
                    "text": (
                        f"RoR at first crack is {actual} F/min (target 12-14). "
                        f"Low RoR may indicate stalling. Maintain steady heat through mid-roast."
                    ),
                })

        elif key == "tp_bt":
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Charge Temp",
                    "text": (
                        f"Turning point at {actual}F is low (target 140-150F). "
                        f"Preheat more aggressively for a higher charge temperature. "
                        f"This will help compress the drying phase."
                    ),
                })

        elif key == "drop_bt":
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"Drop temp {actual}F is below target (375-380F). "
                        f"The roast may taste underdeveloped. Let the roast "
                        f"continue longer after first crack, or carry more "
                        f"heat momentum into crack."
                    ),
                })
            elif "HIGH" in status:
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"Drop temp {actual}F is above target (375-380F). "
                        f"Risk of losing delicate origin flavors. Start "
                        f"cutting heat earlier or drop sooner after first crack."
                    ),
                })

        elif key == "fc_bt":
            if "LOW" in status:
                recs.append({
                    "priority": 2,
                    "category": "Temperature",
                    "text": (
                        f"First crack at {actual}F is below target (358-362F). "
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
                        f"First crack at {actual}F is above target (358-362F). "
                        f"Too much energy going into crack. Reduce heat earlier "
                        f"in the Maillard phase (around 320-330F)."
                    ),
                })

    # RoR smoothness check
    ror_info = metrics.get("ror_smoothness", {})
    if ror_info.get("severity") == "oscillating":
        recs.append({
            "priority": 1,
            "category": "RoR Control",
            "text": (
                f"RoR curve is oscillating ({ror_info['oscillations']} direction changes). "
                f"This causes uneven heat transfer and harsh/smoky notes. "
                f"Reduce heat adjustment frequency and magnitude."
            ),
        })
    elif ror_info.get("severity") == "moderate":
        recs.append({
            "priority": 2,
            "category": "RoR Control",
            "text": (
                f"RoR has moderate oscillation ({ror_info['oscillations']} direction changes). "
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
    """Generate recommendations based on r1-eye sentinel visual data.

    Analyzes the development score trajectory and uniformity to detect
    issues like score plateaus (stalling) and uneven development.
    """
    recs = []
    trajectory = metrics.get("visual_development_scores", [])
    uniformity = metrics.get("visual_uniformity", "unknown")

    if not trajectory or len(trajectory) < 3:
        return recs

    # Check for score plateau — consecutive readings with same score
    # during maillard or development phases (indicates stalling)
    plateau_count = 0
    plateau_score = None
    for i in range(1, len(trajectory)):
        prev = trajectory[i - 1]
        curr = trajectory[i]
        if curr["score"] == prev["score"] and curr["phase"] in ("maillard", "development"):
            if plateau_score == curr["score"]:
                plateau_count += 1
            else:
                plateau_score = curr["score"]
                plateau_count = 1

    if plateau_count >= 3:
        recs.append({
            "priority": 2,
            "category": "Visual Dev",
            "text": (
                f"Visual development stalled at score {plateau_score}/10 for "
                f"{plateau_count + 1} consecutive readings. This may indicate "
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
            recs.append({
                "priority": 2,
                "category": "Visual Dev",
                "text": (
                    f"Rapid visual development jump ({prev['score']} to {curr['score']}) "
                    f"at {_fmt_time(curr['elapsed'])}. This suggests heat was too "
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

    # RoR oscillating or too many heat changes → plan deliberate cuts
    if "Heat Control" in high_pri_cats or "oscillat" in rec_texts:
        actions.append("Plan 2-3 deliberate heat cuts instead of frequent small adjustments")
        seen.add("heat_cuts")

    # Low drop temp → let roast run longer
    if "drop_bt" in off_target and "LOW" in off_target["drop_bt"] and "drop" not in seen:
        actions.append("Let the roast run 15-20 seconds longer after first crack before dropping")
        seen.add("drop")

    # Low FC temp → maintain heat through Maillard
    if "fc_bt" in off_target and "LOW" in off_target["fc_bt"] and "maillard" not in seen:
        actions.append("Maintain steady heat through Maillard — avoid cutting heat before 330F")
        seen.add("maillard")

    # High FC RoR → cut heat earlier
    if "ror_at_fc" in off_target and "HIGH" in off_target["ror_at_fc"] and "heat_cuts" not in seen:
        actions.append("Start cutting heat earlier, around 330-340F, for a gentler approach to first crack")
        seen.add("heat_cuts")

    # Low FC RoR → more momentum
    if "ror_at_fc" in off_target and "LOW" in off_target["ror_at_fc"] and "charge" not in seen:
        actions.append("Carry more heat momentum into first crack — avoid early heat cuts")
        seen.add("charge")

    # High drop temp → drop sooner
    if "drop_bt" in off_target and "HIGH" in off_target["drop_bt"] and "drop" not in seen:
        actions.append("Drop sooner after first crack to preserve origin character")
        seen.add("drop")

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

    # Keys to compare with their labels and whether lower is better
    compare_keys = [
        ("dry_phase_pct", "Drying %", 45.0),
        ("mid_phase_pct", "Maillard %", 40.0),
        ("dev_phase_pct", "Development %", 15.0),
        ("total_time", "Total time (s)", 675),
        ("tp_bt", "Turning point", 145),
        ("fc_bt", "FC temp", 360),
        ("drop_bt", "Drop temp", 377.5),
        ("ror_at_fc", "RoR at FC", 13),
        ("heat_adjustments", "Heat changes", 3),
    ]

    changes = []
    for key, label, ideal in compare_keys:
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


def trend_analysis(all_analyses):
    """Track metric trends across multiple roasts.

    Args:
        all_analyses: List of analysis dicts, ordered chronologically.

    Returns:
        Dict with trend info for key metrics (improving/stable/worsening).
    """
    if len(all_analyses) < 2:
        return {"note": "Need at least 2 roasts for trend analysis"}

    trend_keys = [
        ("dry_phase_pct", 45.0),
        ("mid_phase_pct", 40.0),
        ("dev_phase_pct", 15.0),
        ("ror_at_fc", 13.0),
        ("heat_adjustments", 3),
    ]

    trends = {}
    for key, ideal in trend_keys:
        values = [a["metrics"].get(key, 0) for a in all_analyses]
        distances = [abs(v - ideal) for v in values]

        # Simple trend: compare first half average to second half average
        mid = len(distances) // 2
        first_half = sum(distances[:mid]) / max(mid, 1)
        second_half = sum(distances[mid:]) / max(len(distances) - mid, 1)

        if second_half < first_half * 0.9:
            direction = "improving"
        elif second_half > first_half * 1.1:
            direction = "worsening"
        else:
            direction = "stable"

        trends[key] = {
            "values": values,
            "direction": direction,
            "latest": values[-1],
            "ideal": ideal,
        }

    return trends
