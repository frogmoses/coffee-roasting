"""Roast analysis orchestration.

Extracts the facts of a roast and produces recommendations. Recommendations
come from the LLM recommender (llm_recommender.py), which reasons from roasting
theory and the bean's intended flavor (not from numeric target bands) and ties
advice to the operator's actual machine controls via the control timeline.
"""

from roast_metrics import (
    extract_metrics,
    add_visual_metrics,
    validate_metrics,
)
from llm_recommender import generate_llm_recommendations


def analyze_roast(data, bean_profile=None, visual_data=None):
    """Full analysis of a roast: metrics, recommendations, next-roast actions.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().
        bean_profile: Optional bean profile from coffee_lookup.extract_bean_profile().
        visual_data: Optional visual data from sentinel_loader.extract_visual_data().

    Returns:
        Dict with metrics, recommendations, next_roast, and metadata.
    """
    metrics = extract_metrics(data)

    # Merge sentinel visual data if available
    if visual_data:
        metrics = add_visual_metrics(metrics, visual_data)

    # Validate metrics for suspicious or missing data (recording errors, not
    # taste judgments — e.g. missing CHARGE, FC not marked, drop below FC)
    data_warnings = validate_metrics(metrics)

    # LLM recommender: reads the metrics + control timeline (from data) + bean
    # and visual context and reasons from theory + intended flavor. Fails soft —
    # on no-key/network the scan still records the metrics.
    llm_result, llm_status = generate_llm_recommendations(
        metrics, data, bean_profile
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
        "recommendations": recommendations,
        "next_roast": next_roast,
        "llm_status": llm_status,
        "bean_profile": bean_profile,
        "warnings": data_warnings,
    }


def compare_roasts(analysis1, analysis2):
    """Side-by-side comparison of two roasts.

    Reports the raw change in each metric between two roasts. With no target
    bands there is no "improved/regressed" verdict — direction is purely
    descriptive (increased/decreased), and the roaster judges by taste.

    Args:
        analysis1: First roast analysis dict.
        analysis2: Second roast analysis dict.

    Returns:
        List of change dicts (metric, label, roast1, roast2, delta, direction).
    """
    m1 = analysis1.get("metrics", {})
    m2 = analysis2.get("metrics", {})

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
        v1 = m1.get(key, 0)
        v2 = m2.get(key, 0)
        delta = v2 - v1

        if delta > 0:
            direction = "increased"
        elif delta < 0:
            direction = "decreased"
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
