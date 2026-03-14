"""Terminal output formatting for coffee roast analysis.

Uses Unicode box-drawing characters for clean display.
"""

from roast_metrics import _fmt_time


# Box-drawing characters
H_LINE = "\u2500"     # ─
V_LINE = "\u2502"     # │
TL_CORNER = "\u250c"  # ┌
TR_CORNER = "\u2510"  # ┐
BL_CORNER = "\u2514"  # └
BR_CORNER = "\u2518"  # ┘
T_DOWN = "\u252c"     # ┬
T_UP = "\u2534"       # ┴
T_RIGHT = "\u251c"    # ├
T_LEFT = "\u2524"     # ┤
CROSS = "\u253c"      # ┼


def format_time(seconds):
    """Convert seconds to M:SS format."""
    return _fmt_time(seconds)


def _box_header(title, width=60):
    """Create a boxed header line."""
    padding = width - len(title) - 4
    return f"{TL_CORNER}{H_LINE} {title} {H_LINE * padding}{TR_CORNER}"


def _box_footer(width=60):
    """Create a box footer line."""
    return f"{BL_CORNER}{H_LINE * (width - 2)}{BR_CORNER}"


def _box_row(left, right="", width=60):
    """Create a box row with left-aligned content."""
    content = f" {left}"
    if right:
        pad = width - len(content) - len(right) - 3
        content = f" {left}{' ' * max(pad, 1)}{right}"
    padding = width - len(content) - 2
    return f"{V_LINE}{content}{' ' * max(padding, 0)} {V_LINE}"


def _box_separator(width=60):
    """Create an inner separator line."""
    return f"{T_RIGHT}{H_LINE * (width - 2)}{T_LEFT}"


def _visual_summary(trajectory):
    """Generate a one-line interpretive summary of the visual trajectory.

    Describes the overall shape: steady progression, stall, or rapid jump.

    Args:
        trajectory: List of trajectory point dicts with score/elapsed/phase.

    Returns:
        Summary string, or empty string if insufficient data.
    """
    if len(trajectory) < 2:
        return ""

    scores = [p["score"] for p in trajectory]
    first = scores[0]
    last = scores[-1]

    # Check for plateau/stall: 3+ consecutive same score in maillard/development
    stall_score = None
    stall_phase = None
    run_count = 1
    for i in range(1, len(trajectory)):
        if (trajectory[i]["score"] == trajectory[i - 1]["score"]
                and trajectory[i].get("phase") in ("maillard", "development")):
            run_count += 1
            if run_count >= 3:
                stall_score = trajectory[i]["score"]
                stall_phase = trajectory[i].get("phase", "")
        else:
            run_count = 1

    # Check for rapid jump: any single jump >= 3
    big_jump_time = None
    big_jump_to = None
    for i in range(1, len(trajectory)):
        if trajectory[i]["score"] - trajectory[i - 1]["score"] >= 3:
            big_jump_time = trajectory[i]["elapsed"]
            big_jump_to = trajectory[i]["score"]
            break

    if stall_score is not None:
        return f"Stalled at {stall_score} during {stall_phase}"
    if big_jump_time is not None:
        return f"Rapid jump to {big_jump_to} at {_fmt_time(big_jump_time)}"
    return f"Steady progression {first}\u2192{last}"


def display_roast_summary(analysis):
    """Display a summary of key roast data.

    Args:
        analysis: Analysis dict from roast_analysis.analyze_roast().

    Returns:
        Formatted string.
    """
    m = analysis.get("metrics", {})
    lines = []
    w = 62

    lines.append(_box_header(f"Roast: {analysis.get('title', '?')}", w))

    # Show data quality warnings at the top if any
    data_warnings = analysis.get("warnings", [])
    if data_warnings:
        for warning in data_warnings:
            # Wrap long warnings
            text = f"  !! {warning}"
            while len(text) > w - 4:
                lines.append(_box_row(text[:w-4], "", w))
                text = "     " + text[w-4:]
            lines.append(_box_row(text, "", w))
        lines.append(_box_separator(w))

    lines.append(_box_row(f"Date: {analysis.get('roast_date', '?')}", f"Batch #{analysis.get('batch_nr', '?')}", w))
    lines.append(_box_row(f"Weight: {m.get('weight_in', 0)}g", f"Total: {format_time(m.get('total_time', 0))}", w))
    lines.append(_box_separator(w))

    # Key temperatures
    lines.append(_box_row("Key Temperatures", "", w))
    lines.append(_box_row(f"  Charge BT: {m.get('charge_bt', 0)}F", f"ET: {m.get('charge_et', 0)}F", w))
    lines.append(_box_row(f"  Turning Point: {m.get('tp_bt', 0)}F", f"@ {format_time(m.get('tp_time', 0))}", w))
    lines.append(_box_row(f"  Dry End: {m.get('dry_bt', 0)}F", "", w))
    lines.append(_box_row(f"  First Crack: {m.get('fc_bt', 0)}F", f"@ {format_time(m.get('fc_time', 0))}", w))
    lines.append(_box_row(f"  Drop: {m.get('drop_bt', 0)}F", f"@ {format_time(m.get('drop_time', 0))}", w))
    lines.append(_box_separator(w))

    # Phase breakdown
    lines.append(_box_row("Phase Breakdown", "", w))
    dry_t = format_time(m.get("dry_phase_time", 0))
    mid_t = format_time(m.get("mid_phase_time", 0))
    dev_t = format_time(m.get("dev_phase_time", 0))
    lines.append(_box_row(f"  Drying:      {m.get('dry_phase_pct', 0):5.1f}%", f"({dry_t})", w))
    lines.append(_box_row(f"  Maillard:    {m.get('mid_phase_pct', 0):5.1f}%", f"({mid_t})", w))
    lines.append(_box_row(f"  Development: {m.get('dev_phase_pct', 0):5.1f}%", f"({dev_t})", w))
    lines.append(_box_separator(w))

    # RoR
    lines.append(_box_row("Rate of Rise (F/min)", "", w))
    lines.append(_box_row(f"  At FC: {m.get('ror_at_fc', 0)}", f"Overall: {m.get('total_ror', 0)}", w))
    lines.append(_box_row(f"  Drying: {m.get('dry_phase_ror', 0)}", f"Maillard: {m.get('mid_phase_ror', 0)}", w))
    lines.append(_box_row(f"  Heat adjustments: {m.get('heat_adjustments', 0)}", "", w))
    ror_info = m.get("ror_smoothness", {})
    if ror_info.get("severity"):
        lines.append(_box_row(f"  RoR smoothness: {ror_info['severity']}", "", w))

    # Visual development (from sentinel camera system)
    visual_scores = m.get("visual_development_scores", [])
    if visual_scores:
        lines.append(_box_separator(w))
        source_label = m.get("visual_source", "Sentinel")
        lines.append(_box_row(f"Visual Development ({source_label})", "", w))

        # Group trajectory points by phase for readability
        phase_groups = {}
        phase_order = []
        for entry in visual_scores:
            phase = entry.get("phase", "unknown") or "unknown"
            if phase not in phase_groups:
                phase_groups[phase] = []
                phase_order.append(phase)
            phase_groups[phase].append(entry)

        for phase in phase_order:
            label = phase.capitalize()
            score_line = f"  {label}: "
            for entry in phase_groups[phase]:
                t = format_time(entry["elapsed"])
                s = entry["score"]
                score_line += f"{t}:{s} "
                # Wrap if line gets too long
                if len(score_line) > w - 8:
                    lines.append(_box_row(score_line.rstrip(), "", w))
                    score_line = "    "
            if score_line.strip():
                lines.append(_box_row(score_line.rstrip(), "", w))

        # One-line interpretive summary of trajectory shape
        summary = _visual_summary(visual_scores)
        if summary:
            lines.append(_box_row(f"  {summary}", "", w))

        final = m.get("visual_final_score", 0)
        uniformity = m.get("visual_uniformity", "unknown")
        lines.append(_box_row(f"  Final score: {final}/10", f"Uniformity: {uniformity}", w))

    # Cupping notes
    notes = analysis.get("cupping_notes", "")
    if notes:
        lines.append(_box_separator(w))
        lines.append(_box_row("Cupping Notes", "", w))
        # Wrap long notes
        while len(notes) > w - 6:
            lines.append(_box_row(f"  {notes[:w-6]}", "", w))
            notes = notes[w-6:]
        lines.append(_box_row(f"  {notes}", "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_bean_profile(bean_profile):
    """Display professional bean profile data.

    Args:
        bean_profile: Bean profile dict from coffee_lookup.extract_bean_profile().

    Returns:
        Formatted string, or empty string if no profile.
    """
    if not bean_profile:
        return ""

    lines = []
    w = 62

    lines.append(_box_header(f"Bean: {bean_profile.get('name', '?')}", w))

    # Professional cupping notes
    notes = bean_profile.get("cupping_notes", "")
    if notes:
        lines.append(_box_row("Professional Cupping Notes:", "", w))
        while len(notes) > w - 6:
            lines.append(_box_row(f"  {notes[:w-6]}", "", w))
            notes = notes[w-6:]
        lines.append(_box_row(f"  {notes}", "", w))
        lines.append(_box_separator(w))

    # Overall scores
    overall = bean_profile.get("overall_score", 0)
    chart = bean_profile.get("chart_score", 0)
    if overall or chart:
        lines.append(_box_row(f"Overall: {overall}", f"Chart: {chart}", w))
        lines.append(_box_separator(w))

    # Top flavor dimensions
    dominant = bean_profile.get("dominant_flavors", [])
    if dominant:
        lines.append(_box_row("Top Flavor Dimensions:", "", w))
        for name, score in dominant:
            bar = "\u2588" * score + "\u2591" * (10 - score)  # █ and ░
            lines.append(_box_row(f"  {name:>8}: {bar} {score}/10", "", w))
        lines.append(_box_separator(w))

    # Cupping chart highlights (show non-zero scores)
    cupping = bean_profile.get("cupping_scores", {})
    non_zero = {k: v for k, v in cupping.items() if v > 0}
    if non_zero:
        lines.append(_box_row("Cupping Chart Scores:", "", w))
        for name, score in sorted(non_zero.items(), key=lambda x: -x[1]):
            lines.append(_box_row(f"  {name.replace('_', ' ').title():>16}: {score:.1f}", "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_target_comparison(comparisons):
    """Display a table comparing metrics against targets.

    Args:
        comparisons: From roast_metrics.compare_to_targets().

    Returns:
        Formatted string.
    """
    if not comparisons:
        return "No comparisons available."

    lines = []
    w = 72

    lines.append(_box_header("Target Comparison", w))

    # Table header
    hdr = f"  {'Metric':<20} {'Actual':>10} {'Target':>16} {'Status':>10}"
    lines.append(_box_row(hdr, "", w))
    lines.append(_box_separator(w))

    for comp in comparisons:
        status = comp["status"]
        # Add visual indicator
        if status == "OK":
            indicator = " OK "
        else:
            indicator = status

        row = f"  {comp['label']:<20} {comp['actual_display']:>10} {comp['target_str']:>16} {indicator:>10}"
        lines.append(_box_row(row, "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_recommendations(recs, verbose=False):
    """Display prioritized recommendations.

    Args:
        recs: List of recommendation dicts from roast_analysis.
        verbose: If True, show full_text instead of text for recs that have it.

    Returns:
        Formatted string.
    """
    if not recs:
        return "No recommendations - all metrics on target!"

    lines = []
    w = 72

    lines.append(_box_header("Recommendations", w))

    # Priority legend
    legend = "  [!!!] = fix this first   [ ! ] = worth improving   [   ] = info"
    lines.append(_box_row(legend, "", w))
    lines.append(_box_separator(w))

    for i, rec in enumerate(recs, 1):
        # Priority indicator
        if rec["priority"] == 1:
            pri = "[!!!]"
        elif rec["priority"] == 2:
            pri = "[ ! ]"
        else:
            pri = "[   ]"

        # Category + priority
        header_line = f"  {pri} {i}. [{rec['category']}]"
        lines.append(_box_row(header_line, "", w))

        # Use full_text when verbose and available, otherwise text
        if verbose and "full_text" in rec:
            text = rec["full_text"]
        else:
            text = rec["text"]
        indent = "        "
        max_text_w = w - len(indent) - 4
        while len(text) > max_text_w:
            # Find a good break point
            break_at = text[:max_text_w].rfind(" ")
            if break_at <= 0:
                break_at = max_text_w
            lines.append(_box_row(f"{indent}{text[:break_at]}", "", w))
            text = text[break_at:].lstrip()
        if text:
            lines.append(_box_row(f"{indent}{text}", "", w))

        if i < len(recs):
            lines.append(_box_row("", "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_next_roast(actions):
    """Display a "Next Roast" synthesis box with concrete action items.

    Args:
        actions: List of action strings from generate_next_roast_summary().

    Returns:
        Formatted string, or empty string if no actions.
    """
    if not actions:
        return ""

    lines = []
    w = 72

    lines.append(_box_header("Next Roast: What to Change", w))

    for i, action in enumerate(actions, 1):
        # Wrap long action text
        prefix = f"  {i}. "
        indent = "     "
        text = action
        max_text_w = w - len(indent) - 4
        first_line = True
        while len(text) > max_text_w:
            break_at = text[:max_text_w].rfind(" ")
            if break_at <= 0:
                break_at = max_text_w
            if first_line:
                lines.append(_box_row(f"{prefix}{text[:break_at]}", "", w))
                first_line = False
            else:
                lines.append(_box_row(f"{indent}{text[:break_at]}", "", w))
            text = text[break_at:].lstrip()
        if text:
            if first_line:
                lines.append(_box_row(f"{prefix}{text}", "", w))
            else:
                lines.append(_box_row(f"{indent}{text}", "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_roast_comparison(changes, title1, title2):
    """Display side-by-side roast comparison.

    Args:
        changes: From roast_analysis.compare_roasts().
        title1: Name/ID of first roast.
        title2: Name/ID of second roast.

    Returns:
        Formatted string.
    """
    lines = []
    w = 72

    lines.append(_box_header(f"Compare: {title1} vs {title2}", w))

    hdr = f"  {'Metric':<18} {'Roast 1':>10} {'Roast 2':>10} {'Delta':>8} {'':>12}"
    lines.append(_box_row(hdr, "", w))
    lines.append(_box_separator(w))

    for ch in changes:
        v1 = ch["roast1"]
        v2 = ch["roast2"]
        delta = ch["delta"]
        direction = ch["direction"]

        # Format values
        if ch["metric"] == "total_time":
            v1_str = format_time(v1)
            v2_str = format_time(v2)
            d_str = f"{delta:+.0f}s"
        elif isinstance(v1, float):
            v1_str = f"{v1:.1f}"
            v2_str = f"{v2:.1f}"
            d_str = f"{delta:+.1f}"
        else:
            v1_str = str(v1)
            v2_str = str(v2)
            d_str = f"{delta:+d}" if isinstance(delta, int) else f"{delta:+.1f}"

        # Direction arrow
        if direction == "improved":
            arrow = "  improved"
        elif direction == "regressed":
            arrow = "  regressed"
        else:
            arrow = "  -"

        row = f"  {ch['label']:<18} {v1_str:>10} {v2_str:>10} {d_str:>8} {arrow:>12}"
        lines.append(_box_row(row, "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_trend(analyses):
    """Display simple text trend across roasts.

    Args:
        analyses: List of analysis dicts.

    Returns:
        Formatted string.
    """
    if len(analyses) < 2:
        return "Need at least 2 roasts for trend analysis."

    lines = []
    w = 62

    lines.append(_box_header("Roast Trends", w))

    # Show each roast as a row
    hdr = f"  {'#':<4} {'Date':<12} {'Dry%':>6} {'Mail%':>6} {'Dev%':>6} {'RoR@FC':>7} {'Heat':>5}"
    lines.append(_box_row(hdr, "", w))
    lines.append(_box_separator(w))

    for a in analyses:
        m = a.get("metrics", {})
        row = (
            f"  {a.get('batch_nr', '?'):<4} "
            f"{a.get('roast_date', '?'):<12} "
            f"{m.get('dry_phase_pct', 0):>5.1f}% "
            f"{m.get('mid_phase_pct', 0):>5.1f}% "
            f"{m.get('dev_phase_pct', 0):>5.1f}% "
            f"{m.get('ror_at_fc', 0):>6.1f} "
            f"{m.get('heat_adjustments', 0):>5}"
        )
        lines.append(_box_row(row, "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_roast_list(analyses):
    """Display a list of all analyzed roasts.

    Args:
        analyses: List of analysis dicts.

    Returns:
        Formatted string.
    """
    if not analyses:
        return "No roasts analyzed yet. Run 'scan' first."

    lines = []
    w = 72

    lines.append(_box_header("Analyzed Roasts", w))

    for a in analyses:
        m = a.get("metrics", {})
        roast_id = a.get("roast_id", "?")
        date = a.get("roast_date", "?")
        title = a.get("title", "?")
        total = format_time(m.get("total_time", 0))
        drop = m.get("drop_bt", 0)

        row = f"  #{a.get('batch_nr', '?'):<3} {date}  {title:<30} {total:>6} {drop:>5.0f}F"
        lines.append(_box_row(row, "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)
