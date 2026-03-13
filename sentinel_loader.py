"""Load and match sentinel visual data to roast logs.

Finds sentinel JSON session files from r1-eye and GoPro capture
directories and matches them to .alog roast files by date. Extracts
development score trajectory, final visual score, and uniformity
assessment for integration into the roast analysis pipeline.
"""

import json
import os
from pathlib import Path

# Colon-separated capture directories: SENTINEL_CAPTURES_DIRS=/path/a:/path/b
# Both r1-eye and GoPro sentinel systems produce identical JSON formats
_env_dirs = os.environ.get("SENTINEL_CAPTURES_DIRS", "")
CAPTURE_DIRS = [Path(d) for d in _env_dirs.split(":") if d]


def find_sentinel_logs(captures_dir=None):
    """Find all sentinel JSON session logs across all capture directories.

    Args:
        captures_dir: Override to scan a single directory (for testing).

    Returns:
        List of (session_id, path) tuples sorted by session_id.
    """
    # If a specific dir is given, only scan that one
    dirs_to_scan = [Path(captures_dir)] if captures_dir else CAPTURE_DIRS

    logs = []
    for search_dir in dirs_to_scan:
        if not search_dir.exists():
            continue
        for f in search_dir.glob("sentinel_*.json"):
            # Extract session_id from filename: sentinel_2026-02-17_1851.json
            session_id = f.stem.replace("sentinel_", "")
            logs.append((session_id, f))

    logs.sort(key=lambda x: x[0])
    return logs


def match_sentinel_to_roast(roast_date, roast_time="", captures_dir=None):
    """Find the sentinel session that matches a roast by date.

    Matching strategy:
    1. Extract the date portion from sentinel session_id (YYYY-MM-DD)
    2. Compare against the roast's ISO date
    3. If multiple matches on same date, use closest time match

    Args:
        roast_date: ISO date string from .alog (e.g., "2026-02-17").
        roast_time: Time string from .alog (e.g., "18:51") for tiebreaking.
        captures_dir: Override captures directory.

    Returns:
        Parsed sentinel JSON dict, or None if no match found.
    """
    logs = find_sentinel_logs(captures_dir)
    if not logs:
        return None

    # Find all sessions matching the roast date
    matches = []
    for session_id, path in logs:
        # session_id format: YYYY-MM-DD_HHMM
        session_date = session_id[:10]  # "2026-02-17"
        if session_date == roast_date:
            matches.append((session_id, path))

    if not matches:
        return None

    # If only one match, use it
    if len(matches) == 1:
        return _load_sentinel(matches[0][1])

    # Multiple matches on same day — pick closest time
    if roast_time:
        # Normalize roast_time "18:51" to "1851" for comparison
        roast_hhmm = roast_time.replace(":", "")
        best = None
        best_diff = float("inf")
        for session_id, path in matches:
            session_hhmm = session_id[11:]  # "1851" from "2026-02-17_1851"
            try:
                diff = abs(int(session_hhmm) - int(roast_hhmm))
                if diff < best_diff:
                    best_diff = diff
                    best = path
            except ValueError:
                continue
        if best:
            return _load_sentinel(best)

    # Fallback: return the latest session on that date
    return _load_sentinel(matches[-1][1])


def _load_sentinel(path):
    """Load and parse a sentinel JSON file.

    Args:
        path: Path to the sentinel JSON file.

    Returns:
        Parsed dict, or None on error.
    """
    try:
        return json.loads(Path(path).read_text())
    except (json.JSONDecodeError, OSError):
        return None


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

    return {
        "session_id": sentinel_data.get("session_id", ""),
        "bean_name": sentinel_data.get("bean_name", ""),
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
