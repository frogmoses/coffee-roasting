"""Roast analysis orchestration.

Extracts the facts of a roast and produces recommendations. Recommendations
come from the LLM recommender (llm_recommender.py), which reasons from roasting
theory and the bean's intended flavor (not from numeric target bands) and ties
advice to the operator's actual machine controls via the control timeline.
"""

from roast_parser import parse_alog, extract_roast_data
from roast_metrics import (
    extract_metrics,
    add_visual_metrics,
    validate_metrics,
)
from llm_recommender import generate_llm_recommendations


def analyze_roast(data, bean_profile=None, visual_data=None,
                  cupping_notes=None, prior_roasts=None):
    """Full analysis of a roast: metrics, recommendations, next-roast actions.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().
        bean_profile: Optional bean profile from coffee_lookup.extract_bean_profile().
        visual_data: Optional visual data from sentinel_loader.extract_visual_data().
        cupping_notes: Optional cupping notes from history (added via the
            `cupping` command). Take precedence over the .alog's notes and are
            fed to the LLM so it can judge intended vs actual flavor.
        prior_roasts: Optional list of compact prior-roast dicts (same bean)
            from select_prior_roasts(), giving the LLM the dial-in history.

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

    # CLI-entered cupping notes win over whatever was typed into Artisan —
    # they're the primary cupping workflow and the fresher signal
    effective_notes = cupping_notes or data.get("cupping_notes", "")

    # LLM recommender: reads the metrics + control timeline and BT/RoR curve
    # (from data) + bean, visual, prior-roast, and data-quality context, and
    # reasons from theory + intended-vs-actual flavor. Fails soft — on
    # no-key/network the scan still records the metrics.
    llm_result, llm_status = generate_llm_recommendations(
        metrics, data, bean_profile,
        cupping_notes=effective_notes,
        warnings=data_warnings,
        prior_roasts=prior_roasts,
    )
    recommendations = llm_result["recommendations"] if llm_result else []
    next_roast = llm_result["next_roast"] if llm_result else []

    return {
        "roast_id": data.get("roast_id", ""),
        "title": data.get("title", ""),
        "roast_date": data.get("roast_date", ""),
        "batch_nr": data.get("batch_nr", 0),
        "cupping_notes": effective_notes,
        "roasting_notes": data.get("roasting_notes", ""),
        "metrics": metrics,
        "recommendations": recommendations,
        "next_roast": next_roast,
        "llm_status": llm_status,
        "bean_profile": bean_profile,
        "warnings": data_warnings,
    }


def select_prior_roasts(history, roast_id, title, roast_date, batch_nr, limit=3):
    """Pick up to `limit` earlier roasts of the same bean, oldest first.

    Same bean = same title (case-insensitive); "earlier" is by (date, batch).
    Returns compact dicts — key metrics, the advice given after that roast,
    and how it cupped — sized for the LLM prompt, so the model can see the
    dial-in sequence instead of judging each roast in isolation.

    Args:
        history: Full roast history dict (roast_id -> analysis).
        roast_id: ID of the roast being analyzed (excluded).
        title: Bean title of the roast being analyzed.
        roast_date: ISO date of the roast being analyzed.
        batch_nr: Batch number of the roast being analyzed.
        limit: Max prior roasts to return.

    Returns:
        List of compact prior-roast dicts, oldest first. Empty if none.
    """
    name = (title or "").strip().lower()
    if not name:
        return []
    current_key = (roast_date or "", batch_nr or 0)

    candidates = []
    for rid, entry in history.items():
        if rid == roast_id:
            continue
        if (entry.get("title", "") or "").strip().lower() != name:
            continue
        entry_key = (entry.get("roast_date") or "", entry.get("batch_nr") or 0)
        if entry_key >= current_key:
            continue
        candidates.append((entry_key, entry))

    candidates.sort(key=lambda c: c[0])

    out = []
    for _, entry in candidates[-limit:]:
        metrics = entry.get("metrics", {})
        ror = metrics.get("ror_smoothness", {}) or {}
        out.append({
            "batch_nr": entry.get("batch_nr", 0),
            "roast_date": entry.get("roast_date", ""),
            "metrics": {k: metrics.get(k, 0) for k in (
                "total_time", "fc_time", "fc_bt", "dev_phase_time",
                "dev_phase_pct", "drop_bt", "weight_loss_pct",
                "heat_adjustments",
            )},
            "ror_severity": ror.get("severity", ""),
            "fc_crash": ror.get("fc_crash", False),
            "fc_flick": ror.get("fc_flick", False),
            "ror_rising": ror.get("ror_rising", False),
            "fc_offset": (metrics.get("fc_check") or {}).get("offset"),
            "next_roast": entry.get("next_roast", []),
            "cupping_notes": entry.get("cupping_notes", ""),
        })
    return out


def refresh_recommendations(analysis, history):
    """Re-run the LLM recommender for an already-analyzed roast, in place.

    The scan-time recommendations are generated before the roast has been
    tasted; flavor (intended vs actual) is the primary judging signal, so the
    `cupping` command calls this after notes are saved to close that loop.
    Re-parses the source .alog for the control timeline and curve; reuses the
    cached metrics, bean profile, and warnings. Fails soft — on any problem
    the cached recommendations are left untouched.

    Args:
        analysis: The history entry to refresh (mutated on success).
        history: Full history dict, used to select prior roasts of the bean.

    Returns:
        (updated, status): updated is True when recommendations were replaced;
        status is a short human-readable string.
    """
    source = analysis.get("source_file", "")
    if not source:
        return False, "no source .alog recorded for this roast"
    try:
        data = extract_roast_data(parse_alog(source))
    except (ValueError, FileNotFoundError, KeyError, IndexError, TypeError) as e:
        return False, f"could not re-read {source}: {e}"

    prior_roasts = select_prior_roasts(
        history,
        analysis.get("roast_id", ""),
        analysis.get("title", ""),
        analysis.get("roast_date", ""),
        analysis.get("batch_nr", 0),
    )
    llm_result, status = generate_llm_recommendations(
        analysis.get("metrics", {}), data, analysis.get("bean_profile"),
        cupping_notes=analysis.get("cupping_notes", ""),
        warnings=analysis.get("warnings", []),
        prior_roasts=prior_roasts,
    )
    if not llm_result:
        return False, status

    analysis["recommendations"] = llm_result["recommendations"]
    analysis["next_roast"] = llm_result["next_roast"]
    analysis["llm_status"] = "ok"
    return True, "ok"


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
