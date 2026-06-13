"""Tests for the recommendation engine, sentinel matching, and CLI flows."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import analyze
from roast_analysis import compare_roasts, generate_next_roast_summary, generate_recommendations
from roast_metrics import TARGETS, compare_to_targets
from sentinel_loader import detect_plateau, match_sentinel_to_roast


# --- Recommendation engine ---

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


def test_on_target_roast_yields_no_mechanic_recs():
    metrics = _metrics_with({})
    recs = generate_recommendations(compare_to_targets(metrics), metrics)
    assert recs == []


def test_short_dev_time_recommends_running_longer():
    metrics = _metrics_with({"dev_phase_time": 70, "dev_phase_pct": 12.0})
    recs = generate_recommendations(compare_to_targets(metrics), metrics)
    texts = " ".join(r["text"].lower() for r in recs)
    assert "after fc" in texts or "after first crack" in texts
    # Grouped: dev time + dev pct produce one rec, not two
    phase_recs = [r for r in recs if r["category"] == "Phase Timing"]
    assert len(phase_recs) == 1


def test_high_tp_gets_a_recommendation():
    metrics = _metrics_with({"tp_bt": 185.0})
    recs = generate_recommendations(compare_to_targets(metrics), metrics)
    assert any(r["category"] == "Charge Temp" for r in recs)


def test_flick_rec_outranks_oscillation():
    metrics = _metrics_with({
        "ror_smoothness": {
            "severity": "moderate", "oscillations": 3,
            "heat_correlation": "low_input",
            "fc_crash": True, "fc_flick": True, "crash_min_ror": 2.0,
        },
    })
    recs = generate_recommendations(compare_to_targets(metrics), metrics)
    flick_recs = [r for r in recs if "flick" in r["text"].lower()]
    assert flick_recs and flick_recs[0]["priority"] == 1


def test_weight_loss_on_target_yields_no_rec():
    """A roast inside the weight-loss band produces no development rec."""
    metrics = _metrics_with({"weight_loss_pct": 15.0})
    comps = compare_to_targets(metrics)
    wl = [c for c in comps if c["metric"] == "weight_loss_pct"][0]
    assert wl["status"] == "OK"
    recs = generate_recommendations(comps, metrics)
    assert not any(r["category"] == "Development" for r in recs)


def test_high_weight_loss_recommends_shortening_development():
    metrics = _metrics_with({"weight_loss_pct": 18.0})
    recs = generate_recommendations(compare_to_targets(metrics), metrics)
    dev = [r for r in recs if r["category"] == "Development"]
    assert dev and "high" in dev[0]["text"].lower()


def test_low_weight_loss_recommends_more_development():
    metrics = _metrics_with({"weight_loss_pct": 10.0})
    recs = generate_recommendations(compare_to_targets(metrics), metrics)
    dev = [r for r in recs if r["category"] == "Development"]
    assert dev and "low" in dev[0]["text"].lower()


def test_unrecorded_weight_loss_is_skipped():
    """weight_loss_pct of 0 (no weight-out entered) is not flagged LOW."""
    metrics = _metrics_with({"weight_loss_pct": 0})
    comps = compare_to_targets(metrics)
    assert not any(c["metric"] == "weight_loss_pct" for c in comps)


def test_high_weight_loss_feeds_next_roast_shorten_action():
    metrics = _metrics_with({"weight_loss_pct": 18.0})
    comps = compare_to_targets(metrics)
    recs = generate_recommendations(comps, metrics)
    actions = generate_next_roast_summary(comps, metrics, recs)
    assert any("shorten" in a.lower() for a in actions)


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
