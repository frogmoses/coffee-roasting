"""Tests for the recommendation engine, sentinel matching, and CLI flows."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import analyze
import roast_analysis
from roast_analysis import compare_roasts
from roast_metrics import TARGETS, compare_to_targets
from roast_narrative import build_control_timeline, format_narrative
from sentinel_loader import detect_plateau, match_sentinel_to_roast


# --- Test helpers ---

def _on_target_value(key):
    """An on-target value for a metric, derived from the active TARGETS.

    Keeps the test baseline in sync with any targets.json overrides instead
    of hardcoding numbers that drift when targets are recalibrated.
    """
    t = TARGETS[key]
    if "target" in t:
        return t["target"]
    if "min" in t and "max" in t:
        return round((t["min"] + t["max"]) / 2, 1)
    # Hard-max target (e.g. heat_adjustments) — stay just under the limit
    return max(0, t["max"] - 1)


def _metrics_with(overrides):
    """Baseline on-target metrics dict with selected overrides applied."""
    base = {key: _on_target_value(key) for key in TARGETS}
    base["ror_smoothness"] = {"severity": "smooth"}
    base.update(overrides)
    return base


# --- Control timeline reconstruction (the LLM recommender's key input) ---

def _timeline_data():
    """Synthetic roast with a few control moves, one after DROP."""
    timex = [float(i * 2) for i in range(15)]  # 0..28s
    bt = [200.0 + i for i in range(15)]
    return {
        "timex": timex,
        "bt": bt,
        "heater": [100.0] * 15,
        "fan": [0.0] * 15,
        # CHARGE=0, DRY=3, FCs=6, ..., DROP=10
        "timeindex": [0, 3, 6, 0, 0, 0, 10, 14],
        "events": [
            {"index": 0, "type": 3, "percentage": 100, "abs_time": 0.0},
            {"index": 3, "type": 3, "percentage": 90, "abs_time": 6.0},
            {"index": 6, "type": 0, "percentage": 20, "abs_time": 12.0},
            # After DROP (idx 10) — must be excluded from the timeline
            {"index": 12, "type": 0, "percentage": 100, "abs_time": 24.0},
        ],
    }


def test_timeline_excludes_post_drop_moves():
    timeline = build_control_timeline(_timeline_data())
    moves = timeline["moves"]
    assert len(moves) == 3  # the post-DROP fan move is dropped
    assert all(m["rel_time"] <= 20 for m in moves)  # <= DROP time (timex[10])


def test_timeline_annotates_phase_markers():
    timeline = build_control_timeline(_timeline_data())
    by_marker = {m["marker"]: m for m in timeline["moves"]}
    assert by_marker["DRY"]["percentage"] == 90
    assert by_marker["FCs"]["control"] == "Fan"
    assert timeline["start_heater"] == 100.0


def test_format_narrative_renders_moves():
    text = format_narrative(build_control_timeline(_timeline_data()))
    assert "DRY" in text and "Heater" in text and "Fan" in text
    # Three control moves -> three "->" lines
    assert text.count("->") == 3


def test_timeline_handles_missing_data():
    timeline = build_control_timeline({"events": [], "timex": [], "timeindex": []})
    assert timeline["moves"] == []
    assert format_narrative(timeline).startswith("No control moves")


# --- Roast comparison ---

def test_compare_roasts_ideals_follow_targets():
    """Comparison ideals derive from TARGETS — moving toward the dev-time
    target range midpoint counts as improvement."""
    lo = TARGETS["dev_phase_time"]["min"]
    hi = TARGETS["dev_phase_time"]["max"]
    mid = (lo + hi) / 2
    a1 = {"metrics": _metrics_with({"dev_phase_time": lo - 30})}
    a2 = {"metrics": _metrics_with({"dev_phase_time": mid})}
    changes = compare_roasts(a1, a2)
    devt = [c for c in changes if c["metric"] == "dev_phase_time"][0]
    assert devt["direction"] == "improved"


# --- Shared plateau detection ---

def test_detect_plateau_finds_stall():
    trajectory = [
        {"elapsed": 60 * i, "score": s, "phase": "maillard"}
        for i, s in enumerate([3, 4, 5, 5, 5, 6])
    ]
    plateau = detect_plateau(trajectory)
    assert plateau["score"] == 5
    assert plateau["run"] == 3


def test_detect_plateau_ignores_drying():
    trajectory = [
        {"elapsed": 30 * i, "score": 1, "phase": "drying"}
        for i in range(5)
    ]
    assert detect_plateau(trajectory) is None


# --- Sentinel matching ---

def _write_sentinel(directory, session_id, uuid=""):
    payload = {
        "session_id": session_id,
        "roast_uuid": uuid,
        "observations": [
            {"elapsed_seconds": 10, "phase": "drying",
             "development_score": 2, "uniformity": "consistent"},
        ],
    }
    path = directory / f"sentinel_{session_id}.json"
    path.write_text(json.dumps(payload))
    return path


def test_uuid_match_beats_date_match(tmp_path):
    _write_sentinel(tmp_path, "2026-05-06_1900", uuid="other-roast")
    _write_sentinel(tmp_path, "2026-05-07_1200", uuid="abc123")
    result = match_sentinel_to_roast(
        "2026-05-06", "19:00", "abc123", captures_dir=tmp_path)
    assert result["session_id"] == "2026-05-07_1200"


def test_time_tiebreak_picks_closest_session(tmp_path):
    _write_sentinel(tmp_path, "2026-05-06_1848")
    _write_sentinel(tmp_path, "2026-05-06_1917")
    result = match_sentinel_to_roast(
        "2026-05-06", "19:15", "", captures_dir=tmp_path)
    assert result["session_id"] == "2026-05-06_1917"


# --- CLI flows (scan, history, resolution) ---

def _write_alog(directory, name, batch_nr, title, date, time_str):
    """Write a minimal but parseable .alog (Python dict literal)."""
    timex = [float(i * 2) for i in range(400)]
    bt = [250.0 - i for i in range(20)] + [230.0 + i * 0.4 for i in range(380)]
    raw = {
        "title": title,
        "roastbatchnr": batch_nr,
        "roastisodate": date,
        "roasttime": time_str,
        "roastUUID": f"uuid-{name}",
        "timex": timex,
        "temp2": bt,
        "temp1": [v + 40 for v in bt],
        "timeindex": [0, 100, 300, 0, 0, 0, 399, 0],
        "computed": {
            "totaltime": 700, "dryphasetime": 380, "midphasetime": 215,
            "finishphasetime": 105, "CHARGE_BT": 250.0, "TP_BT": 160.0,
            "FCs_BT": 360.0, "FCs_time": 600.0, "DROP_BT": 380.0,
            "DROP_time": 700.0, "fcs_ror": 16.0,
        },
    }
    path = directory / f"{name}.alog"
    path.write_text(repr(raw))
    return path


def _patched_env(tmp_path, monkeypatch):
    """Point analyze at a temp logs dir and history file."""
    logs = tmp_path / "roast-logs"
    logs.mkdir()
    monkeypatch.setattr(analyze, "LOGS_DIR", logs)
    monkeypatch.setattr(analyze, "HISTORY_FILE", tmp_path / "history.json")
    # No find-coffee or sentinel lookups during tests
    monkeypatch.delenv("FIND_COFFEE_URL", raising=False)
    monkeypatch.delenv("SENTINEL_CAPTURES_DIRS", raising=False)
    # Stub the LLM recommender so scan tests stay offline and deterministic
    monkeypatch.setattr(
        roast_analysis, "generate_llm_recommendations",
        lambda *a, **k: (None, "test-stub"),
    )
    return logs


class _Args:
    """Stand-in for argparse Namespace."""
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def test_scan_survives_corrupt_alog(tmp_path, monkeypatch, capsys):
    logs = _patched_env(tmp_path, monkeypatch)
    (logs / "bad.alog").write_text("not a python dict {{{")
    _write_alog(logs, "good", 1, "Test Bean", "2026-06-01", "10:00")
    analyze.cmd_scan(_Args(force=False))
    history = analyze.load_history()
    assert len(history) == 1
    assert "Skipping bad.alog" in capsys.readouterr().out


def test_force_rescan_preserves_cupping_notes(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "roast", 1, "Test Bean", "2026-06-01", "10:00")
    analyze.cmd_scan(_Args(force=False))

    # Add cupping notes the way cmd_cupping does
    history = analyze.load_history()
    rid = list(history)[0]
    history[rid]["cupping_notes"] = "Bright berry, clean finish"
    analyze.save_history(history)

    analyze.cmd_scan(_Args(force=True))
    history = analyze.load_history()
    assert history[rid]["cupping_notes"] == "Bright berry, clean finish"


def test_id_collision_gets_time_suffix(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    # Same batch number, title, and date — different files (real case:
    # two '#3' roasts on 2026-05-06)
    _write_alog(logs, "a_first", 3, "Same Bean", "2026-06-01", "18:48")
    _write_alog(logs, "b_second", 3, "Same Bean", "2026-06-01", "19:17")
    analyze.cmd_scan(_Args(force=False))
    history = analyze.load_history()
    assert len(history) == 2


def test_resolve_partial_match_prefers_latest(tmp_path, monkeypatch):
    history = {
        "1_Ethiopia Gerba_2026-04-13": {
            "roast_id": "1_Ethiopia Gerba_2026-04-13",
            "roast_date": "2026-04-13", "batch_nr": 1,
        },
        "5_Ethiopia Gerba_2026-05-06": {
            "roast_id": "5_Ethiopia Gerba_2026-05-06",
            "roast_date": "2026-05-06", "batch_nr": 5,
        },
    }
    assert analyze.resolve_roast_id(history, "Ethiopia") == "5_Ethiopia Gerba_2026-05-06"
