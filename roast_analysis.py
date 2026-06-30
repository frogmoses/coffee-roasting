"""Roast analysis orchestration.

Extracts metrics, compares them against targets, and produces recommendations.
Recommendations come from the LLM recommender (llm_recommender.py), which reads
the full roast picture — including the move-by-move control timeline — and
returns advice tied to the operator's actual machine controls. The old
fixed-template engine has been removed in favor of that.
"""

from roast_metrics import (
    TARGETS,
    extract_metrics,
    add_visual_metrics,
    compare_to_targets,
    validate_metrics,
)
from llm_recommender import generate_llm_recommendations


def _target_ideal(key):
    """Single ideal value for a metric (range midpoint for min/max targets).

    Used by compare_roasts() to score whether a metric moved toward or away
    from target between two roasts.
    """
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
        Dict with metrics, comparisons, recommendations, next_roast, and metadata.
    """
    metrics = extract_metrics(data)

    # Merge sentinel visual data if available
    if visual_data:
        metrics = add_visual_metrics(metrics, visual_data)

    # Validate metrics for suspicious or missing data
    data_warnings = validate_metrics(metrics)

    comparisons = compare_to_targets(metrics)

    # LLM recommender: reads metrics + comparisons + the control timeline (from
    # data) + bean/visual context and returns advice tied to actual dial moves.
    # Fails soft — on no-key/network the scan still records metrics/comparisons.
    llm_result, llm_status = generate_llm_recommendations(
        metrics, comparisons, data, bean_profile
    )
    recommendations = llm_result["recommendations"] if llm_result else []
    next_roast = llm_result["next_roast"] if llm_result else []

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
        "next_roast": next_roast,
        "llm_status": llm_status,
        "bean_profile": bean_profile,
        "warnings": data_warnings,
    }


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
        ("weight_loss_pct", "Weight loss %"),
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
