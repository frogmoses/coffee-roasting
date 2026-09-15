"""Terminal output formatting for coffee roast analysis.

Uses Unicode box-drawing characters for clean display.
"""

from roast_metrics import _fmt_time
from sentinel_loader import detect_plateau


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

    # Check for plateau/stall via the shared detector so the summary and
    # the recommendation engine always agree on what counts as a stall
    plateau = detect_plateau(trajectory)
    stall_score = plateau["score"] if plateau else None
    stall_phase = plateau["phase"] if plateau else None

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
    # Weight: show in -> out and roast loss % once weight-out has been entered
    weight_out = m.get("weight_out", 0)
    if weight_out:
        weight_str = f"Weight: {m.get('weight_in', 0)}g -> {weight_out}g ({m.get('weight_loss_pct', 0)}% loss)"
    else:
        weight_str = f"Weight: {m.get('weight_in', 0)}g"
    lines.append(_box_row(weight_str, f"Total: {format_time(m.get('total_time', 0))}", w))
    # Ambient conditions, only once they've been entered in Artisan
    if m.get("ambient_temp"):
        ambient = f"Ambient: {m['ambient_temp']:g}F"
        if m.get("ambient_humidity"):
            ambient += f", {m['ambient_humidity']:g}% RH"
        lines.append(_box_row(ambient, "", w))
    lines.append(_box_separator(w))

    # Key temperatures
    lines.append(_box_row("Key Temperatures", "", w))
    lines.append(_box_row(f"  Charge BT: {m.get('charge_bt', 0)}F", f"ET: {m.get('charge_et', 0)}F", w))
    lines.append(_box_row(f"  Turning Point: {m.get('tp_bt', 0)}F", f"@ {format_time(m.get('tp_time', 0))}", w))
    lines.append(_box_row(f"  Dry End: {m.get('dry_bt', 0)}F", "", w))
    lines.append(_box_row(f"  First Crack: {m.get('fc_bt', 0)}F", f"@ {format_time(m.get('fc_time', 0))}", w))
    lines.append(_box_row(f"  Drop: {m.get('drop_bt', 0)}F", f"@ {format_time(m.get('drop_time', 0))}", w))
    # Warn if CHARGE wasn't recorded — RoR math for drying will be off
    if not m.get("charge_bt"):
        lines.append(_box_row("  ! CHARGE temperature not recorded - mark CHARGE manually next roast.", "", w))
    lines.append(_box_separator(w))

    # Phase breakdown
    lines.append(_box_row("Phase Breakdown", "", w))
    dry_t = format_time(m.get("dry_phase_time", 0))
    mid_t = format_time(m.get("mid_phase_time", 0))
    dev_t = format_time(m.get("dev_phase_time", 0))
    dry_ror = m.get("dry_phase_ror", 0)
    mid_ror = m.get("mid_phase_ror", 0)
    dev_ror = m.get("dev_phase_ror", 0)
    lines.append(_box_row(f"  Drying:      {m.get('dry_phase_pct', 0):5.1f}%", f"({dry_t} @ {dry_ror} F/min)", w))
    lines.append(_box_row(f"  Maillard:    {m.get('mid_phase_pct', 0):5.1f}%", f"({mid_t} @ {mid_ror} F/min)", w))
    lines.append(_box_row(f"  Development: {m.get('dev_phase_pct', 0):5.1f}%", f"({dev_t} @ {dev_ror} F/min)", w))
    lines.append(_box_separator(w))

    # RoR
    lines.append(_box_row("Rate of Rise (F/min)", "", w))
    lines.append(_box_row(f"  At FC: {m.get('ror_at_fc', 0)}", f"Overall: {m.get('total_ror', 0)}", w))
    lines.append(_box_row(f"  Drying: {m.get('dry_phase_ror', 0)}", f"Maillard: {m.get('mid_phase_ror', 0)}", w))
    # Curve-detected FC: where the steam-release RoR crash sits vs the mark
    fc_check = m.get("fc_check")
    if fc_check:
        if fc_check.get("offset") is None:
            lines.append(_box_row(
                f"  FC by curve: {format_time(fc_check['detected_time'])} @ {fc_check['detected_bt']:.0f}F (no mark to compare)", "", w))
        else:
            off = fc_check["offset"]
            rel = "at mark" if abs(off) <= 5 else (f"{abs(off)}s before mark" if off < 0 else f"{off}s after mark")
            flag = "  ! check mark" if fc_check.get("mark_suspect") else ""
            lines.append(_box_row(
                f"  FC by curve: {format_time(fc_check['detected_time'])} @ {fc_check['detected_bt']:.0f}F, {rel}{flag}", "", w))
    # Microphone first-crack verdict (ear sidecar), beside the curve check
    fc_audio = m.get("fc_audio")
    if fc_audio:
        if fc_audio.get("detected_time") is None:
            lines.append(_box_row(
                f"  FC by audio: none ({fc_audio.get('cracks_after_arm', 0)} armed cracks"
                + (f", peak {fc_audio['peak_cpm']:.0f}/min, rule {fc_audio.get('rule_cpm', 0)}"
                   if fc_audio.get("peak_cpm") else "") + ")", "", w))
        else:
            off = fc_audio.get("offset")
            if off is None:
                rel = "no mark to compare"
            elif abs(off) <= 5:
                rel = "at mark"
            else:
                rel = f"{abs(off)}s before mark" if off < 0 else f"{off}s after mark"
            bt = f" @ {fc_audio['detected_bt']:.0f}F" if fc_audio.get("detected_bt") is not None else ""
            rate = f" ({fc_audio['peak_cpm']:.0f}/min)" if fc_audio.get("peak_cpm") else ""
            flag = "  ! check mark" if fc_audio.get("mark_suspect") else ""
            # Verdict rebuilt from the recording when the ear had no live clock
            how = " [offline]" if fc_audio.get("detected_source") == "offline" else ""
            lines.append(_box_row(
                f"  FC by audio: {format_time(fc_audio['detected_time'])}{bt}, {rel}{rate}{flag}{how}", "", w))
    lines.append(_box_row(f"  Heat adjustments: {m.get('heat_adjustments', 0)}", "", w))
    ror_info = m.get("ror_smoothness", {})
    if ror_info.get("severity"):
        # Show heat context alongside smoothness assessment
        heat_corr = ror_info.get("heat_correlation", "unknown")
        heat_count = m.get("heat_adjustments", 0)
        if heat_corr == "low_input":
            context = "(natural curve variation)"
        elif heat_corr == "high_input":
            context = f"({heat_count} heat changes)"
        else:
            context = f"({heat_count} heat changes)"
        lines.append(_box_row(f"  RoR smoothness: {ror_info['severity']} {context}", "", w))
        # Rao's 2nd rule: a rising Maillard RoR means the curve isn't decelerating
        if ror_info.get("ror_rising"):
            lines.append(_box_row(f"  ! RoR rising in Maillard (+{ror_info.get('ror_rise', 0)} F/min) - should decelerate", "", w))

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


def display_recommendations(recs, verbose=False):
    """Display prioritized recommendations.

    Args:
        recs: List of recommendation dicts from roast_analysis.
        verbose: If True, show full_text instead of text for recs that have it.

    Returns:
        Formatted string.
    """
    if not recs:
        return "No recommendations."

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
        actions: List of next-roast action strings (LLM-generated, cached).

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

        # Direction is descriptive only — no target means no "better/worse"
        if direction == "increased":
            arrow = "  up"
        elif direction == "decreased":
            arrow = "  down"
        else:
            arrow = "  -"

        row = f"  {ch['label']:<18} {v1_str:>10} {v2_str:>10} {d_str:>8} {arrow:>12}"
        lines.append(_box_row(row, "", w))

    lines.append(_box_footer(w))
    return "\n".join(lines)


def _fc_offset_cell(metrics):
    """Trend-table cell: curve-detected FC minus the mark, e.g. '+14s'."""
    check = metrics.get("fc_check") or {}
    off = check.get("offset")
    if off is None:
        return "--"
    return f"{off:+d}s"


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
    hdr = f"  {'#':<3} {'Date':<10} {'Dry%':>6} {'Mail%':>6} {'Dev%':>6} {'RoR@FC':>7} {'Heat':>5} {'FCoff':>6}"
    lines.append(_box_row(hdr, "", w))
    lines.append(_box_separator(w))

    for a in analyses:
        m = a.get("metrics", {})
        row = (
            f"  {a.get('batch_nr', '?'):<3} "
            f"{a.get('roast_date', '?'):<10} "
            f"{m.get('dry_phase_pct', 0):>5.1f}% "
            f"{m.get('mid_phase_pct', 0):>5.1f}% "
            f"{m.get('dev_phase_pct', 0):>5.1f}% "
            f"{m.get('ror_at_fc', 0):>6.1f} "
            f"{m.get('heat_adjustments', 0):>5} "
            f"{_fc_offset_cell(m):>6}"
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


def _wrap_rows(text, width, prefix="  ", indent="  "):
    """Wrap text into box rows (first line with prefix, rest with indent)."""
    rows = []
    max_w = width - len(indent) - 4
    first = True
    while len(text) > max_w:
        cut = text[:max_w].rfind(" ")
        if cut <= 0:
            cut = max_w
        rows.append(_box_row(f"{prefix if first else indent}{text[:cut]}", "", width))
        text = text[cut:].lstrip()
        first = False
    if text:
        rows.append(_box_row(f"{prefix if first else indent}{text}", "", width))
    return rows


def display_contrast_plan(plan):
    """Render a contrast-set plan from roast_plan.build_contrast_plan().

    Printable: the shared recipe through FC, then one line per batch with
    its FC->DROP seconds and projected drop temperature, then the notes
    (FC spread, heat-soak shift, safety).
    """
    w = 72
    lines = [_box_header(f"Contrast Set: {plan['bean']}", w)]
    lines += _wrap_rows(
        f"Recipe from batch #{plan['anchor_batch']} ({plan['anchor_date']}). "
        "Same moves through FC; only the FC->DROP seconds change.", w, prefix=" ", indent=" ")
    stats = plan["fc_stats"]
    if stats["count"]:
        lines.append(_box_row(
            f"FC on this recipe: ~{format_time(stats['fc_time_mean'])} "
            f"@ {stats['fc_bt_mean']:.0f}F over {stats['count']} roast(s)", "", w))
    lines.append(_box_separator(w))

    base = plan["base_schedule"]
    moves = base.get("moves", [])
    start = base.get("start") or {}
    lines.append(_box_row("Shared schedule (time from CHARGE):", "", w))
    # The timeline usually logs the charge heater setting as a move at 0:00;
    # only synthesize a start row when it doesn't.
    if start.get("heater") is not None and not any(m.get("marker") == "CHARGE" for m in moves):
        lines.append(_box_row(
            f"   0:00           Heater {start['heater']:.0f}%  Fan {start.get('fan') or 0:.0f}%  [CHARGE]", "", w))
    for mv in moves:
        marker = f"  [{mv['marker']}]" if mv.get("marker") else ""
        bt = f"BT {mv['bt']:.0f}F" if mv.get("bt") is not None else ""
        lines.append(_box_row(
            f"  {format_time(mv['rel_time']):>5}  {bt:<9} {mv['control']} -> {mv['percentage']:.0f}%{marker}",
            "", w))
    if not moves:
        lines.append(_box_row("  (no schedule available — see notes)", "", w))
    for mv in plan.get("post_fc_moves", []):
        lines.append(_box_row(
            f"  FC+{mv['after_fc']:.0f}s  {mv['control']} -> {mv['percentage']:.0f}%  (keep for every batch)",
            "", w))
    lines.append(_box_separator(w))

    lines.append(_box_row("Batches, in roasting order (drop timed from FC):", "", w))
    for c in plan["contrasts"]:
        lines.append(_box_row(
            f"  {c['order']}. FC + {c['dev_s']:>3}s  -> drop ~{c['projected_drop_bt']}F   {c['label']}",
            "", w))
        if c["unsafe"]:
            lines.append(_box_row(
                f"        !! near eject: only {c['eject_margin']}F under the safety limit", "", w))
    if plan["notes"]:
        lines.append(_box_separator(w))
        for note in plan["notes"]:
            lines += _wrap_rows("- " + note, w, prefix="  ", indent="    ")
    lines.append(_box_footer(w))
    return "\n".join(lines)


# ---------------------------------------------------------------- profiles

def display_profile(profile, envelope=None):
    """Render a designed roast profile (roast_profile.py) as a printable box.

    The schedule table comes from roast_schedule.schedule_rows(); each move
    shows when it is expected, at what BT, the trigger kind, and its reason.
    """
    from roast_schedule import schedule_rows, fmt_mmss
    w = 72
    sched = profile.get("schedule", {})
    status = profile.get("status", "draft")
    lines = [_box_header(f"Roast Profile: {profile.get('bean', '?')}  v{profile.get('version', '?')}  [{status}]", w)]
    based = profile.get("based_on") or {}
    if based:
        kind = based.get("kind", "")
        if kind == "roast":
            src = f"the logged moves of {based.get('roast_id')}"
        elif kind == "revision":
            src = f"v{based.get('version')} revised from its cupping" + (
                f" (reference v{based['reference']})" if based.get("reference") else "")
        elif kind == "design":
            src = "designed against the measured staircase"
        elif kind == "staircase":
            src = "the measured staircase (designer unavailable)"
        else:
            src = kind
        if based.get("borrowed_from"):
            src += f", envelope borrowed from {based['borrowed_from']}"
        lines += _wrap_rows("Based on: " + src, w, prefix=" ", indent="   ")
    pred = profile.get("predicted") or sched.get("predicted") or {}
    if pred:
        conf = pred.get("confidence", "")
        lines.append(_box_row(
            f" Predicted: FC ~{fmt_mmss(pred.get('fc_time_s'))} @ {pred.get('fc_bt', '?')}F, "
            f"drop ~{pred.get('drop_bt', '?')}F after {fmt_mmss(sched.get('drop', {}).get('seconds'))} dev"
            + (f"  ({conf})" if conf else ""), "", w))
    lines.append(_box_separator(w))
    lines.append(_box_row(" Schedule (expected time from CHARGE, expected BT):", "", w))
    for r in schedule_rows(sched, envelope):
        bt = f"{r['bt']:.0f}F" if r.get("bt") is not None else ""
        kind = {"bt": "BT", "time": "clock", "manual": "you"}.get(r["kind"], r["kind"])
        lines.append(_box_row(f"  {r['when']:>7}  {bt:>5}  {r['text']}  [{kind}]", "", w))
        if r.get("why"):
            lines += _wrap_rows(r["why"], w, prefix="                 - ", indent="                   ")
    warm = (sched.get("charge") or {}).get("warmup") or {}
    if warm:
        lines.append(_box_separator(w))
        lines += _wrap_rows(
            f"Warm-up: motor on, heater 100% to {warm.get('preheat_bt', 370)}F, then heater "
            f"{warm.get('cool_heater', 0)}% / fan {warm.get('cool_fan', 100)}% down to "
            f"{warm.get('charge_bt', 300)}F, heater 100%, fan 0%, charge.", w, prefix=" ", indent="   ")
    guards = sched.get("guards") or {}
    if guards:
        lines += _wrap_rows(
            f"Guards: pop-up at {guards.get('fc_popup_bt')}F, DROP timer only once BT > "
            f"{guards.get('fc_min_bt')}F, DROP if BT > {guards.get('bt_ceiling')}F.", w, prefix=" ", indent="   ")
    hyp = profile.get("flavor_hypothesis")
    if hyp:
        lines.append(_box_separator(w))
        lines += _wrap_rows("Expect in the cup: " + hyp, w, prefix=" ", indent="   ")
    lever = profile.get("contrast_lever")
    if lever:
        vals = ", ".join(str(v) for v in lever.get("values", []))
        lines += _wrap_rows(f"Contrast set varies {lever.get('key')}: {vals}. {lever.get('why', '')}",
                            w, prefix=" ", indent="   ")
    for item in profile.get("watch_for") or []:
        lines += _wrap_rows("Watch for: " + item, w, prefix=" ", indent="   ")
    changes = profile.get("changes_from_baseline") or []
    if changes:
        lines.append(_box_separator(w))
        lines.append(_box_row(" Changes from the baseline:", "", w))
        for c in changes:
            lines += _wrap_rows(f"{c.get('what')}: {c.get('from')} -> {c.get('to')}"
                                + (f"  ({c['why']})" if c.get("why") else ""), w, prefix="  - ", indent="    ")
    val = profile.get("validator") or {}
    if val.get("warnings") or val.get("clamped"):
        lines.append(_box_separator(w))
        for c in val.get("clamped", []):
            lines += _wrap_rows("clamped: " + c, w, prefix="  ", indent="    ")
        for wmsg in val.get("warnings", []):
            lines += _wrap_rows("! " + wmsg, w, prefix="  ", indent="    ")
    roasts = profile.get("roasts") or []
    if roasts:
        lines.append(_box_separator(w))
        lines.append(_box_row(" Roasted on this version:", "", w))
        for r in roasts:
            dev = f"dev {r['dev_s']:.0f}s" if r.get("dev_s") else "dev ?"
            verdict = ""
            intake = r.get("cupping_intake") or {}
            if intake:
                verdict = (f"  cup: balance {intake.get('balance')}, astr {intake.get('astringency')}, "
                           f"{intake.get('preference') or ''} {intake.get('drink_again') or ''}").rstrip()
            lines += _wrap_rows(f"#{r.get('batch_nr')} {r.get('roast_date')} {dev}"
                                + (f"  {r['execution']}" if r.get("execution") else "") + verdict,
                                w, prefix="  ", indent="    ")
    exp = profile.get("exported")
    if exp:
        lines.append(_box_separator(w))
        lines += _wrap_rows(f"Exported {exp.get('at', '')[:16]} mode {exp.get('mode')}"
                            + (", DROP manual" if exp.get("manual_drop") else
                               f", ear latency {exp.get('ear_latency_s')}s"),
                            w, prefix=" ", indent="   ")
    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_alarm_rows(rows, title="Artisan alarm set"):
    """Render exported alarm rows (alarm_export.describe_rows) in a box."""
    from alarm_export import describe_rows
    w = 72
    lines = [_box_header(title, w)]
    for line in describe_rows(rows):
        lines += _wrap_rows(line, w, prefix=" ", indent="       ")
    lines.append(_box_separator(w))
    lines += _wrap_rows("* = beeps.  [if #N] fires only after row N; [unless #N] only if row N "
                        "has not fired.  A row with both a BT and a time fires on whichever "
                        "comes first.", w, prefix=" ", indent="   ")
    lines.append(_box_footer(w))
    return "\n".join(lines)


def display_execution(execution):
    """Render roast_schedule.compare_execution(): designed vs executed."""
    from roast_schedule import fmt_mmss
    w = 72
    lines = [_box_header("Designed vs Executed", w)]
    for r in execution.get("moves", []):
        plan = f"{fmt_mmss(r.get('expected_s'))}"
        if r.get("expected_bt") is not None:
            plan += f"/{r['expected_bt']:.0f}F"
        name = f"{r['action'].capitalize()} {r['value']}%"
        if r.get("missed"):
            lines.append(_box_row(f"  {name:<12} planned {plan:<12} MISSED", "", w))
            continue
        ran = f"{fmt_mmss(r.get('executed_s'))}"
        if r.get("executed_bt") is not None:
            ran += f"/{r['executed_bt']:.0f}F"
        deltas = []
        if r.get("delta_s") is not None:
            deltas.append(f"{r['delta_s']:+d}s")
        if r.get("delta_f") is not None:
            deltas.append(f"{r['delta_f']:+d}F")
        lines.append(_box_row(f"  {name:<12} planned {plan:<12} ran {ran:<12} {' '.join(deltas)}", "", w))
    for x in execution.get("extra", []):
        bt = f"/{x['bt']:.0f}F" if x.get("bt") is not None else ""
        lines.append(_box_row(f"  unplanned: {x['action']} {x['value']}% at {fmt_mmss(x['at_s'])}{bt}", "", w))
    lines.append(_box_separator(w))
    d = execution.get("dev", {})
    if d.get("designed_s") is not None:
        actual = f"{d['actual_s']:.0f}s" if d.get("actual_s") else "?"
        delta = f" ({d['delta_s']:+d}s)" if d.get("delta_s") is not None else ""
        lines.append(_box_row(f"  Development: designed {d['designed_s']}s, actual {actual}{delta}", "", w))
    f = execution.get("fc", {})
    if f.get("predicted_s"):
        lines.append(_box_row(
            f"  First crack: predicted {fmt_mmss(f['predicted_s'])} @ {f.get('predicted_bt', '?')}F, "
            f"actual {fmt_mmss(f.get('actual_s'))} @ {f.get('actual_bt') or '?'}F", "", w))
    dr = execution.get("drop", {})
    if dr.get("predicted_bt"):
        lines.append(_box_row(f"  Drop: predicted {dr['predicted_bt']}F, actual {dr.get('actual_bt') or '?'}F", "", w))
    lines += _wrap_rows(execution.get("summary", ""), w, prefix="  ", indent="    ")
    lines.append(_box_footer(w))
    return "\n".join(lines)
