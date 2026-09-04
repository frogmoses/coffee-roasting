"""Load and match sentinel visual data to roast logs.

Finds sentinel JSON session files from sentinel capture directories
(GoPro, r1-eye, etc.) and matches them to .alog roast files by date.
Extracts development score trajectory, final visual score, and
uniformity assessment for integration into the roast analysis pipeline.
"""

import json
import os
from pathlib import Path

# Colon-separated capture directories: SENTINEL_CAPTURES_DIRS=/path/a:/path/b
# Both r1-eye and GoPro sentinel systems produce identical JSON formats
_env_dirs = os.environ.get("SENTINEL_CAPTURES_DIRS", "")
CAPTURE_DIRS = [Path(os.path.expanduser(d)) for d in _env_dirs.split(":") if d]


def find_session_logs(dirs, prefix):
    """Find `<prefix>YYYY-MM-DD_HHMM.json` session files in the given dirs.

    Shared by the visual sentinel (prefix "sentinel_") and the ear's crack
    sidecars (prefix "crack_"), which use the same session_id convention.

    Returns:
        List of (session_id, path) tuples sorted by session_id.
    """
    logs = []
    for search_dir in dirs:
        search_dir = Path(search_dir)
        if not search_dir.exists():
            continue
        for f in search_dir.glob(f"{prefix}*.json"):
            logs.append((f.stem[len(prefix):], f))
    logs.sort(key=lambda x: x[0])
    return logs


def find_sentinel_logs(captures_dir=None):
    """Find all sentinel JSON session logs across all capture directories.

    Args:
        captures_dir: Override to scan a single directory (for testing).

    Returns:
        List of (session_id, path) tuples sorted by session_id.
    """
    dirs_to_scan = [Path(captures_dir)] if captures_dir else CAPTURE_DIRS
    return find_session_logs(dirs_to_scan, "sentinel_")


def match_session_to_roast(logs, roast_date, roast_time="", roast_uuid=""):
    """Pick the session log that matches a roast from a (session_id, path) list.

    Matching strategy (in priority order):
    1. Deterministic: match on roast_uuid if both the log and .alog have it
    2. Date match: session_id starts with the roast's YYYY-MM-DD
    3. Time tiebreak: multiple matches on the same date -> closest HHMM

    Returns:
        Parsed JSON dict (with _source_path), or None if no match.
    """
    if not logs:
        return None

    # Priority 1: deterministic UUID match
    if roast_uuid:
        for session_id, path in logs:
            data = load_json_cached(path)
            if data and data.get("roast_uuid") == roast_uuid:
                return data

    # Priority 2: date-based matching with time tiebreak
    matches = [(sid, path) for sid, path in logs if sid[:10] == roast_date]
    if not matches:
        return None
    if len(matches) == 1:
        return load_json_cached(matches[0][1])

    # Multiple matches on the same day — pick the closest HHMM. .alog times
    # can carry seconds ("18:51:07"), so compare only the HH:MM part.
    if roast_time:
        roast_hhmm = roast_time[:5].replace(":", "")
        best = None
        best_diff = float("inf")
        for session_id, path in matches:
            session_hhmm = session_id[11:15]
            try:
                diff = abs(int(session_hhmm) - int(roast_hhmm))
            except ValueError:
                continue
            if diff < best_diff:
                best_diff = diff
                best = path
        if best:
            return load_json_cached(best)

    # Fallback: the latest session on that date
    return load_json_cached(matches[-1][1])


def match_sentinel_to_roast(roast_date, roast_time="", roast_uuid="", captures_dir=None):
    """Find the sentinel session that matches a roast (see match_session_to_roast).

    Args:
        roast_date: ISO date string from .alog (e.g., "2026-02-17").
        roast_time: Time string from .alog (e.g., "18:51") for tiebreaking.
        roast_uuid: UUID from .alog roastUUID field for deterministic matching.
        captures_dir: Override captures directory.

    Returns:
        Parsed sentinel JSON dict, or None if no match found.
    """
    return match_session_to_roast(find_sentinel_logs(captures_dir), roast_date, roast_time, roast_uuid)


# Cache of parsed session files keyed by path, invalidated on mtime change.
# UUID matching scans every file per roast, so a scan over N roasts would
# otherwise re-parse each JSON N times.
_sentinel_cache = {}


def _load_sentinel(path):
    """Backward-compatible alias for load_json_cached()."""
    return load_json_cached(path)


def load_json_cached(path):
    """Load and parse a session JSON file (cached by path + mtime).

    Injects _source_path into the returned dict so downstream code
    can determine which sentinel system produced the data.

    Args:
        path: Path to the sentinel JSON file.

    Returns:
        Parsed dict with _source_path added, or None on error.
    """
    key = str(path)
    try:
        mtime = os.path.getmtime(key)
        cached = _sentinel_cache.get(key)
        if cached and cached[0] == mtime:
            return cached[1]
        data = json.loads(Path(path).read_text())
        # In-memory annotation for source labeling (not written back to JSON)
        data["_source_path"] = key
        _sentinel_cache[key] = (mtime, data)
        return data
    except (json.JSONDecodeError, OSError):
        return None


def detect_plateau(trajectory, min_run=3):
    """Find the longest run of consecutive identical development scores
    during maillard/development (a stall signature).

    Shared by the display summary and the recommendation engine so both
    report the same stall with the same threshold.

    Args:
        trajectory: List of trajectory point dicts (elapsed/score/phase).
        min_run: Minimum consecutive same-score readings to count as a stall.

    Returns:
        Dict with score, run (reading count), phase, and start_index of the
        longest qualifying run, or None if no plateau reaches min_run.
    """
    best = None
    run = 1
    start = 0
    for i in range(1, len(trajectory)):
        same = trajectory[i]["score"] == trajectory[i - 1]["score"]
        in_phase = trajectory[i].get("phase") in ("maillard", "development")
        if same and in_phase:
            if run == 1:
                start = i - 1
            run += 1
            if run >= min_run and (best is None or run > best["run"]):
                best = {
                    "score": trajectory[i]["score"],
                    "run": run,
                    "phase": trajectory[i].get("phase", ""),
                    "start_index": start,
                }
        else:
            run = 1
    return best


def _infer_source_label(path_str):
    """Derive a display label from the sentinel file's directory path.

    Uses the grandparent directory name to identify the sentinel system:
    gopro/captures/file.json -> "GoPro", r1-eye/captures/file.json -> "r1-eye".

    Args:
        path_str: File path string to the sentinel JSON.

    Returns:
        Human-readable source label string.
    """
    if not path_str:
        return "Sentinel"

    p = Path(path_str)
    # Walk up to find a recognizable directory name
    for part in reversed(p.parts):
        lower = part.lower()
        if "gopro" in lower:
            return "GoPro"
        if "r1-eye" in lower or "r1_eye" in lower:
            return "r1-eye"

    return "Sentinel"


def extract_visual_data(sentinel_data):
    """Extract visual metrics from sentinel session data.

    Pulls out the development score trajectory, final score,
    and uniformity assessment from the observation sequence.

    Args:
        sentinel_data: Parsed sentinel JSON dict.

    Returns:
        Dict with visual metrics, or None if no observations.
    """
    if not sentinel_data:
        return None

    observations = sentinel_data.get("observations", [])
    if not observations:
        return None

    # Build score trajectory (elapsed_seconds, score) for non-zero scores
    trajectory = []
    for obs in observations:
        score = obs.get("development_score", 0)
        elapsed = obs.get("elapsed_seconds", 0)
        if score > 0:
            trajectory.append({
                "elapsed": elapsed,
                "score": score,
                "phase": obs.get("phase", ""),
            })

    # Final observation with a non-zero score
    final_obs = None
    for obs in reversed(observations):
        if obs.get("development_score", 0) > 0:
            final_obs = obs
            break

    # Assess uniformity from observations
    uniformity_notes = [
        obs.get("uniformity", "")
        for obs in observations
        if obs.get("uniformity")
    ]

    # Classify uniformity from the text descriptions
    uniformity_rating = _classify_uniformity(uniformity_notes)

    # Infer which sentinel system produced this data
    source_label = _infer_source_label(sentinel_data.get("_source_path", ""))

    return {
        "session_id": sentinel_data.get("session_id", ""),
        "bean_name": sentinel_data.get("bean_name", ""),
        "visual_source": source_label,
        "trajectory": trajectory,
        "score_count": len(trajectory),
        "final_score": final_obs.get("development_score", 0) if final_obs else 0,
        "final_color": final_obs.get("color_assessment", "") if final_obs else "",
        "uniformity_rating": uniformity_rating,
        "uniformity_notes": uniformity_notes[-1] if uniformity_notes else "",
        "artisan_events": sentinel_data.get("artisan_events", {}),
    }


def _classify_uniformity(notes):
    """Classify uniformity from vision assessment text.

    Scans the uniformity notes for keywords to produce a simple rating.

    Args:
        notes: List of uniformity description strings.

    Returns:
        One of: "excellent", "good", "moderate", "poor", or "unknown".
    """
    if not notes:
        return "unknown"

    # Count keyword occurrences across all notes
    excellent_count = 0
    good_count = 0
    moderate_count = 0
    poor_count = 0

    for note in notes:
        lower = note.lower()
        if "excellent" in lower or "highly uniform" in lower:
            excellent_count += 1
        elif "good" in lower or "consistent" in lower:
            good_count += 1
        elif "moderate" in lower or "noticeable variation" in lower:
            moderate_count += 1
        elif "poor" in lower or "significant variation" in lower or "uneven" in lower:
            poor_count += 1

    # Return the most common rating
    counts = [
        (excellent_count, "excellent"),
        (good_count, "good"),
        (moderate_count, "moderate"),
        (poor_count, "poor"),
    ]
    counts.sort(key=lambda x: -x[0])

    # Need at least one categorized note
    if counts[0][0] > 0:
        return counts[0][1]
    return "unknown"


def enrich_trajectory_with_temps(visual_data, roast_data):
    """Add BT and ET from .alog data to each trajectory point.

    Matches each visual observation's elapsed time against the .alog
    time series to find the closest temperature reading.

    Args:
        visual_data: Visual data dict from extract_visual_data().
        roast_data: Extracted roast data from roast_parser.extract_roast_data().

    Returns:
        The visual_data dict (modified in place), or None if inputs invalid.
    """
    if not visual_data or not roast_data:
        return visual_data

    trajectory = visual_data.get("trajectory", [])
    if not trajectory:
        return visual_data

    timex = roast_data.get("timex", [])
    bt = roast_data.get("bt", [])
    et = roast_data.get("et", [])
    timeindex = roast_data.get("timeindex", [])

    if not timex or not bt or len(timeindex) < 1:
        return visual_data

    # CHARGE time is the zero reference for elapsed seconds
    charge_idx = timeindex[0]
    if charge_idx >= len(timex):
        return visual_data
    charge_time = timex[charge_idx]

    for point in trajectory:
        # Target absolute time = charge time + elapsed seconds from sentinel
        target_time = charge_time + point["elapsed"]

        # Find the closest timex index (linear scan — arrays are small)
        best_idx = 0
        best_diff = abs(timex[0] - target_time)
        for i in range(1, len(timex)):
            diff = abs(timex[i] - target_time)
            if diff < best_diff:
                best_diff = diff
                best_idx = i

        # Add temperatures if the match is within 5 seconds
        if best_diff <= 5:
            if best_idx < len(bt):
                point["bt"] = round(bt[best_idx], 1)
            if best_idx < len(et):
                point["et"] = round(et[best_idx], 1)

    return visual_data
