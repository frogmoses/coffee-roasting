"""Load the ear's crack sidecars and match them to roast logs.

The ear (ear/ on the roaster) writes crack_YYYY-MM-DD_HHMM.json beside a
WAV for every roast: each detected click with wall-clock epoch and seconds
since CHARGE, plus the first-crack verdict from its rate rule. This module
finds the sidecar for a roast (roastUUID first, then date/time, same as the
visual sentinel), rebuilds the roast clock if the WebSocket link was down,
and reduces it to the flat `fc_audio` dict the analysis stores beside the
curve-based `fc_check`.

CRACK_CAPTURES_DIR is read at call time (not import time) so tests can
point it at a temp dir; unset, it defaults to ear/captures in this repo.
"""

import os
from pathlib import Path

from roast_metrics import FC_MARK_TOLERANCE, _fmt_time
from sentinel_loader import find_session_logs, load_json_cached, match_session_to_roast

_PREFIX = "crack_"


# Where the roaster's ear pushes sidecars by default: ear/captures in this
# repo (gitignored). CRACK_CAPTURES_DIR overrides it.
DEFAULT_CAPTURES_DIR = Path(__file__).parent / "ear" / "captures"


def capture_dir():
    """The captures directory: CRACK_CAPTURES_DIR if set, else ear/captures."""
    d = os.environ.get("CRACK_CAPTURES_DIR", "")
    return Path(os.path.expanduser(d)) if d else DEFAULT_CAPTURES_DIR


def find_crack_logs(captures_dir=None):
    """(session_id, path) for every crack_*.json, sorted by session_id."""
    d = Path(captures_dir) if captures_dir else capture_dir()
    return find_session_logs([d] if d else [], _PREFIX)


def match_crack_to_roast(roast_date, roast_time="", roast_uuid="", captures_dir=None):
    """The sidecar for a roast: UUID match, else same date with closest time."""
    logs = find_crack_logs(captures_dir)
    return match_session_to_roast(logs, roast_date, roast_time, roast_uuid)


def anchor_charge_epoch(crack_data, roast_data):
    """Wall-clock epoch of CHARGE for this session.

    Prefer the sidecar's own charge_epoch (set from Artisan's CHARGE
    message). Fall back to the .alog's roastepoch plus the CHARGE sample's
    timex: roastepoch is Artisan's ON time (verified on the first live day,
    CHARGE landed 135-165 s after it), and timex counts from ON.
    """
    epoch = (crack_data or {}).get("charge_epoch")
    if epoch:
        return float(epoch)
    roast_epoch = (roast_data or {}).get("roast_epoch") or 0
    if not roast_epoch:
        return None
    # Measured on the first live day: roastepoch is Artisan's ON time, and
    # CHARGE came 135-165 s later — timex[charge_idx] seconds, since timex
    # counts from ON.
    timex = (roast_data or {}).get("timex") or []
    timeindex = (roast_data or {}).get("timeindex") or []
    charge_idx = max(timeindex[0], 0) if timeindex else 0
    offset = timex[charge_idx] if charge_idx < len(timex) else 0.0
    return float(roast_epoch) + float(offset)


def _elapsed(crack, charge_epoch):
    """Seconds since CHARGE for a crack, from its stored elapsed or its epoch."""
    if crack.get("elapsed") is not None:
        return crack["elapsed"]
    if charge_epoch and crack.get("epoch") is not None:
        return crack["epoch"] - charge_epoch
    return None


def _bt_at(roast_data, elapsed):
    """BT nearest to `elapsed` seconds after CHARGE, within 5 s, else None."""
    timex = roast_data.get("timex", [])
    bt = roast_data.get("bt", [])
    timeindex = roast_data.get("timeindex", [])
    if elapsed is None or not timex or not bt or not timeindex:
        return None
    charge_idx = max(timeindex[0], 0)
    if charge_idx >= len(timex):
        return None
    target = timex[charge_idx] + elapsed
    best = min(range(len(timex)), key=lambda i: abs(timex[i] - target))
    if abs(timex[best] - target) <= 5 and best < len(bt):
        return round(bt[best], 1)
    return None


def _dry_time(roast_data):
    """Operator's DRY mark in seconds after CHARGE, or None."""
    timex = roast_data.get("timex", [])
    timeindex = roast_data.get("timeindex", [])
    if len(timeindex) < 2 or timeindex[1] <= 0 or timeindex[1] >= len(timex):
        return None
    charge_idx = max(timeindex[0], 0)
    return timex[timeindex[1]] - timex[charge_idx]


# The ear's own fallback arm point (ear_session.ARM_FALLBACK_S) and its live
# rule defaults (crack_detector.FC_RULE_DEFAULTS), mirrored here so the
# analyzer stays independent of the ear package.
OFFLINE_ARM_FALLBACK_S = 480.0
DEFAULT_FC_RULE = {"n": 7, "window_s": 20.0, "min_elapsed_s": 480.0}


def _effective_arm(crack_data, roast_data):
    """(armed_at, source) for counting cracks.

    The sidecar's arm time is used when the ear had a roast clock. An arm at
    0.0 means it had none (it joined after CHARGE and armed on DRY with no
    CHARGE reference), so fall back to the .alog's DRY mark, then to the
    ear's CHARGE+480 s fallback.
    """
    armed = (crack_data or {}).get("armed") or {}
    if armed.get("elapsed"):
        return float(armed["elapsed"]), armed.get("source", "")
    dry = _dry_time(roast_data or {})
    if dry is not None:
        return float(dry), "DRY (from .alog)"
    return OFFLINE_ARM_FALLBACK_S, f"CHARGE+{OFFLINE_ARM_FALLBACK_S:.0f}s (fallback)"


def run_fc_rule(times, armed_at, rule=None):
    """Offline replica of the ear's FirstCrackTracker rate rule.

    `times` are crack times in seconds after CHARGE (any order, None
    skipped). First crack is declared at the first crack that makes `n`
    cracks inside the trailing `window_s`; the onset is the first crack of
    that window. Cracks before `armed_at` or the rule's minimum elapsed time
    are ignored, exactly as the live tracker does. Returns the fc_detected
    dict (tagged source="offline") or None.
    """
    params = dict(DEFAULT_FC_RULE)
    params.update({k: v for k, v in (rule or {}).items() if v is not None})
    n = int(params["n"])
    window = float(params["window_s"])
    gate = max(float(armed_at), float(params["min_elapsed_s"]))
    accepted = sorted(t for t in times if t is not None and t >= gate)
    if not accepted:
        return None
    declared = None
    peak_cpm, peak_at = 0.0, None
    for i, t in enumerate(accepted):
        in_window = [u for u in accepted[:i + 1] if u > t - window]
        cpm = len(in_window) * 60.0 / window
        if cpm > peak_cpm:
            peak_cpm, peak_at = cpm, t
        if declared is None and len(in_window) >= n:
            declared = {
                "elapsed": round(in_window[0], 1),
                "first_crack_elapsed": round(accepted[0], 1),
                "count_in_window": len(in_window),
                "window_s": window,
                "n": n,
                "declared_at_elapsed": round(t, 1),
            }
    if declared is None:
        return None
    declared["peak_cpm"] = round(peak_cpm, 1)
    declared["peak_cpm_elapsed"] = round(peak_at, 1)
    declared["source"] = "offline"
    return declared


def peak_crack_rate(times, window_s=DEFAULT_FC_RULE["window_s"]):
    """(cracks per minute, time) of the densest `window_s` window in `times`.

    Reported even when the rate rule never fires, so "no first crack" comes
    with how close the recording got (batch #25, 2026-09-13: 12/min against
    a rule that needs 21/min).
    """
    accepted = sorted(t for t in times if t is not None)
    peak, at = 0.0, None
    for i, t in enumerate(accepted):
        count = sum(1 for u in accepted[:i + 1] if u > t - window_s)
        cpm = count * 60.0 / window_s
        if cpm > peak:
            peak, at = cpm, t
    return (round(peak, 1), round(at, 1)) if at is not None else (None, None)


def _mark_time(roast_data):
    """Operator's FCs mark in seconds after CHARGE, or None."""
    timex = roast_data.get("timex", [])
    timeindex = roast_data.get("timeindex", [])
    if len(timeindex) < 3 or timeindex[2] <= 0 or timeindex[2] >= len(timex):
        return None
    charge_idx = max(timeindex[0], 0)
    return timex[timeindex[2]] - timex[charge_idx]


def extract_audio_data(crack_data, roast_data):
    """Reduce a sidecar to the flat fc_audio dict.

    Returns None when there is no sidecar or it holds neither cracks nor a
    first-crack verdict.
    """
    if not crack_data:
        return None
    cracks = crack_data.get("cracks") or []
    fc = crack_data.get("fc_detected")
    if not cracks and not fc:
        return None

    charge_epoch = anchor_charge_epoch(crack_data, roast_data)
    times = [_elapsed(c, charge_epoch) for c in cracks]
    armed_at, armed_source = _effective_arm(crack_data, roast_data)
    after_arm = [t for c, t in zip(cracks, times)
                 if t is not None and (c.get("armed") or t >= armed_at)]

    # No live verdict (the ear had no roast clock, or the WebSocket was
    # down): re-run the same rate rule over the re-anchored crack times.
    detected_source = "live" if fc else None
    if not fc and after_arm:
        fc = run_fc_rule(after_arm, armed_at, crack_data.get("fc_rule"))
        if fc:
            detected_source = "offline"
    rule = dict(DEFAULT_FC_RULE)
    rule.update({k: v for k, v in (crack_data.get("fc_rule") or {}).items() if v is not None})
    peak_cpm, peak_cpm_time = peak_crack_rate(after_arm, rule["window_s"])
    if fc and fc.get("peak_cpm"):
        peak_cpm, peak_cpm_time = fc["peak_cpm"], fc.get("peak_cpm_elapsed")

    detected_time = None
    if fc:
        detected_time = fc.get("elapsed")
        if detected_time is None and charge_epoch and fc.get("epoch"):
            detected_time = fc["epoch"] - charge_epoch
    mark_time = _mark_time(roast_data)
    offset = None
    if detected_time is not None and mark_time is not None:
        offset = round(detected_time - mark_time)

    capture = crack_data.get("capture") or {}
    out = {
        "session_id": crack_data.get("session_id", ""),
        "mode": crack_data.get("mode", ""),
        "crack_count": len(cracks),
        "cracks_after_arm": len(after_arm),
        "armed_at": armed_at,
        "armed_source": armed_source,
        "detected_source": detected_source,
        "detected_time": round(detected_time) if detected_time is not None else None,
        "detected_bt": _bt_at(roast_data, detected_time),
        "first_crack_time": (fc or {}).get("first_crack_elapsed"),
        "mark_time": round(mark_time) if mark_time is not None else None,
        "offset": offset,
        "mark_suspect": offset is not None and abs(offset) > FC_MARK_TOLERANCE,
        "peak_cpm": peak_cpm,
        "peak_cpm_time": peak_cpm_time,
        # What the rule needed, so a miss can be read as "how close"
        "rule_cpm": round(rule["n"] * 60.0 / rule["window_s"]),
        "capture": {
            "peak_dbfs": capture.get("peak_dbfs"),
            "clipped_blocks": capture.get("clipped_blocks"),
            "overflows": capture.get("overflows"),
        },
    }
    out["details"] = _details(out)
    return out


def _details(a):
    """One human sentence for the display and the LLM."""
    if a["detected_time"] is None:
        line = (f"microphone heard no first crack ({a['cracks_after_arm']} clicks after arming, "
                f"{a['crack_count']} total")
        if a.get("peak_cpm") and a.get("peak_cpm_time") is not None:
            line += (f"; densest {a['peak_cpm']:.0f}/min at {_fmt_time(a['peak_cpm_time'])}, "
                     f"rule needs {a.get('rule_cpm', 0)}/min")
        return line + ")"
    where = f" ({a['detected_bt']:.0f}F)" if a.get("detected_bt") is not None else ""
    rate = f", peak {a['peak_cpm']:.0f}/min" if a.get("peak_cpm") else ""
    line = f"microphone puts first crack at {_fmt_time(a['detected_time'])}{where}{rate}"
    offline = " (rule re-run offline)" if a.get("detected_source") == "offline" else ""
    if a["offset"] is None:
        return line + "; FC not marked" + offline
    off = a["offset"]
    if abs(off) <= 5:
        rel = "right at your mark"
    elif off < 0:
        rel = f"{abs(off)}s before your mark"
    else:
        rel = f"{off}s after your mark"
    return f"{line}, {rel}{offline}"
