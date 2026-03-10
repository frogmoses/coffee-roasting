"""Parser for Artisan .alog roast log files.

Artisan saves roast data as Python dict literals (not JSON).
We use ast.literal_eval() for safe parsing.
"""

import ast
from pathlib import Path


def parse_alog(filepath):
    """Read and parse an .alog file into a Python dict.

    Args:
        filepath: Path to the .alog file.

    Returns:
        Parsed dict of the roast data.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        ValueError: If the file can't be parsed.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")

    text = path.read_text(encoding="utf-8")
    try:
        return ast.literal_eval(text)
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"Failed to parse {filepath}: {e}")


def extract_roast_data(raw):
    """Pull out the fields we need from a raw .alog dict.

    Args:
        raw: Dict from parse_alog().

    Returns:
        Dict with extracted roast data fields.
    """
    computed = raw.get("computed", {})
    timex = raw.get("timex", [])
    timeindex = raw.get("timeindex", [])  # [CHARGE, DRY, FCs, FCe, SCs, SCe, DROP, COOL]

    # Get BT (temp2) and ET (temp1) arrays
    bt = raw.get("temp2", [])
    et = raw.get("temp1", [])

    # Heater profile is extratemp1[0], Fan profile is extratemp2[0]
    heater = raw.get("extratemp1", [[]])[0] if raw.get("extratemp1") else []
    fan = raw.get("extratemp2", [[]])[0] if raw.get("extratemp2") else []

    # Decode special events
    events = _decode_events(raw, timex, timeindex)

    # Build roast ID from filename-friendly title + date
    title = raw.get("title", "Unknown")
    roast_date = raw.get("roastisodate", "")
    batch_nr = raw.get("roastbatchnr", 0)

    return {
        "title": title,
        "roast_date": roast_date,
        "roast_time": raw.get("roasttime", ""),
        "batch_nr": batch_nr,
        "roast_id": f"{batch_nr}_{title}_{roast_date}" if batch_nr else f"{title}_{roast_date}",
        "weight_in": raw.get("weight", [0, 0, "g"])[0],
        "weight_unit": raw.get("weight", [0, 0, "g"])[2],
        "roaster": raw.get("machinesetup", raw.get("roastertype", "")),
        "mode": raw.get("mode", "F"),  # F or C

        # Time arrays
        "timex": timex,
        "timeindex": timeindex,

        # Temperature arrays
        "bt": bt,
        "et": et,
        "heater": heater,
        "fan": fan,

        # Events
        "events": events,

        # Computed values from Artisan
        "computed": computed,

        # Notes
        "roasting_notes": raw.get("roastingnotes", ""),
        "cupping_notes": raw.get("cuppingnotes", ""),

        # Artisan flavors (spider chart)
        "flavors": raw.get("flavors", []),
        "flavor_labels": raw.get("flavorlabels", []),

        # Roast characteristics
        "heavy_fc": raw.get("heavyFC", False),
        "low_fc": raw.get("lowFC", False),
        "oily": raw.get("oily", False),
        "tipping": raw.get("tipping", False),
        "scorching": raw.get("scorching", False),
    }


def _decode_events(raw, timex, timeindex):
    """Decode special events into a structured list.

    Event types: 0=Fan, 1=Drum, 2=Damper, 3=Heater
    Event value: percentage = (value - 1) * 10

    Args:
        raw: The raw .alog dict.
        timex: Time array.
        timeindex: Array of key moment indices.

    Returns:
        List of event dicts with type, percentage, time, and index.
    """
    event_indices = raw.get("specialevents", [])
    event_types = raw.get("specialeventstype", [])
    event_values = raw.get("specialeventsvalue", [])
    event_strings = raw.get("specialeventsStrings", [])
    type_names = raw.get("etypes", ["Fan", "Drum", "Damper", "Heater", "--"])

    # CHARGE index for calculating relative time
    charge_idx = timeindex[0] if timeindex else 0
    charge_time = timex[charge_idx] if charge_idx < len(timex) else 0

    events = []
    for i in range(len(event_indices)):
        idx = event_indices[i]
        etype = event_types[i] if i < len(event_types) else 4
        evalue = event_values[i] if i < len(event_values) else 0
        estring = event_strings[i] if i < len(event_strings) else ""

        # Decode percentage: (value - 1) * 10
        percentage = (evalue - 1) * 10

        # Get absolute and relative time
        abs_time = timex[idx] if idx < len(timex) else 0
        rel_time = abs_time - charge_time

        events.append({
            "index": idx,
            "type": etype,
            "type_name": type_names[etype] if etype < len(type_names) else "Unknown",
            "value": evalue,
            "percentage": percentage,
            "string": estring,
            "abs_time": abs_time,
            "rel_time": rel_time,
        })

    return events


def scan_roast_logs(directory):
    """Find all .alog files in a directory, sorted by date.

    Args:
        directory: Path to the roast-logs directory.

    Returns:
        List of Path objects sorted by filename (which includes date).
    """
    log_dir = Path(directory)
    if not log_dir.exists():
        return []

    alogs = list(log_dir.glob("*.alog"))
    # Sort by filename which includes date
    alogs.sort(key=lambda p: p.name)
    return alogs
