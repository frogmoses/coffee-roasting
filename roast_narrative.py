"""Control-timeline reconstruction for coffee roast analysis.

The .alog file records every control move the roaster made — heater, fan,
damper, and drum changes — as timestamped events, plus full per-sample
heater/fan setpoint arrays. The old template engine collapsed all of that
into a single integer ("heat_adjustments"). This module reconstructs the
move-by-move sequence so the LLM recommender can reason about *what the
roaster actually did* (e.g. "heater held 100% for 6.5 min into drying"),
not just the off-target outcomes.

The sequence is bounded to CHARGE->DROP so the post-drop cooling ramp
(fan spin-down, sensor garbage) never leaks into the narrative.
"""

# specialeventstype codes in the .alog (also exposed via etypes)
_TYPE_NAMES = {0: "Fan", 1: "Drum", 2: "Damper", 3: "Heater"}


def build_control_timeline(data):
    """Reconstruct the CHARGE->DROP control moves as a structured list.

    Args:
        data: Extracted roast data from roast_parser.extract_roast_data().

    Returns:
        Dict with:
          - "moves": list of {rel_time, bt, control, percentage, marker}
            for each control change between CHARGE and DROP, in time order
          - "start_heater"/"start_fan": setpoints at CHARGE (the moves list
            only shows *changes*, so the initial state matters)
          - "phase_marks": {index: name} for CHARGE/DRY/FCs/DROP
        Returns an empty-ish dict when there isn't enough data to reconstruct.
    """
    events = data.get("events", [])
    timex = data.get("timex", [])
    timeindex = data.get("timeindex", [])
    bt = data.get("bt", [])
    heater = data.get("heater", [])
    fan = data.get("fan", [])

    if not timex or len(timeindex) < 7:
        return {"moves": [], "start_heater": None, "start_fan": None, "phase_marks": {}}

    charge_idx = max(timeindex[0], 0)  # Artisan uses -1 for "CHARGE not set"
    drop_idx = timeindex[6] if timeindex[6] > 0 else len(timex) - 1

    # Phase markers keyed by their index into timex, so a move that lands on a
    # phase boundary (e.g. the heater cut at the DRY mark) can be annotated.
    phase_marks = {}
    labels = ["CHARGE", "DRY", "FCs", "FCe", "SCs", "SCe", "DROP"]
    for i, label in enumerate(labels):
        if i < len(timeindex) and timeindex[i] > 0:
            phase_marks[timeindex[i]] = label
    phase_marks.setdefault(charge_idx, "CHARGE")

    charge_time = timex[charge_idx] if charge_idx < len(timex) else 0

    moves = []
    for ev in events:
        idx = ev.get("index", -1)
        # Only moves made between CHARGE and DROP — this drops the cooling
        # ramp and any post-drop sensor noise.
        if not (charge_idx <= idx <= drop_idx):
            continue
        etype = ev.get("type")
        control = _TYPE_NAMES.get(etype)
        if control is None:
            continue  # unknown control code (only the four known dials matter)
        rel = ev.get("abs_time", 0) - charge_time
        bt_at = bt[idx] if 0 <= idx < len(bt) else None
        moves.append({
            "rel_time": rel,
            "bt": round(bt_at, 1) if bt_at is not None else None,
            "control": control,
            "percentage": ev.get("percentage"),
            "marker": phase_marks.get(idx, ""),
        })

    moves.sort(key=lambda m: m["rel_time"])

    start_heater = heater[charge_idx] if 0 <= charge_idx < len(heater) else None
    start_fan = fan[charge_idx] if 0 <= charge_idx < len(fan) else None

    return {
        "moves": moves,
        "start_heater": start_heater,
        "start_fan": start_fan,
        "phase_marks": phase_marks,
    }


def _fmt_clock(seconds):
    """Format relative seconds as M:SS (negative values clamp to 0:00)."""
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def format_narrative(timeline):
    """Render a control timeline as a compact text block for the LLM prompt.

    Produces lines like:
        0:00  BT 300F  Heater -> 100%   [CHARGE]
        6:31  BT 300F  Heater ->  90%   [DRY]
        9:07  BT 348F  Heater ->  80%

    Args:
        timeline: Dict from build_control_timeline().

    Returns:
        Human/LLM-readable multi-line string, or a short note if no moves.
    """
    moves = timeline.get("moves", [])
    if not moves:
        return "No control moves recorded between CHARGE and DROP."

    lines = []
    start_heater = timeline.get("start_heater")
    start_fan = timeline.get("start_fan")
    start_bits = []
    if start_heater is not None:
        start_bits.append(f"heater {start_heater:g}%")
    if start_fan is not None:
        start_bits.append(f"fan {start_fan:g}%")
    if start_bits:
        lines.append(f"At CHARGE: {', '.join(start_bits)}")

    for m in moves:
        bt_str = f"BT {m['bt']:g}F" if m["bt"] is not None else "BT --"
        pct = m["percentage"]
        pct_str = f"{pct:g}%" if pct is not None else "?"
        marker = f"   [{m['marker']}]" if m["marker"] else ""
        lines.append(
            f"{_fmt_clock(m['rel_time']):>5}  {bt_str:<9}  "
            f"{m['control']:<7} -> {pct_str:>4}{marker}"
        )

    return "\n".join(lines)
