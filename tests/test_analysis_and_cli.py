"""Tests for the recommendation engine, sentinel matching, and CLI flows."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import analyze
import roast_analysis
from roast_analysis import compare_roasts, select_prior_roasts
from roast_metrics import build_curve_series
from roast_narrative import build_control_timeline, format_narrative
from sentinel_loader import detect_plateau, match_sentinel_to_roast


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


# --- BT/RoR curve series (the LLM's raw view of the curve) ---

def _curve_data():
    """Synthetic 800s roast, 2s sampling: CHARGE=0, DRY=100, FCs=300, DROP=399."""
    timex = [float(i * 2) for i in range(400)]
    bt = [250.0 - i for i in range(20)] + [230.0 + i * 0.4 for i in range(380)]
    return {
        "timex": timex,
        "bt": bt,
        "timeindex": [0, 100, 300, 0, 0, 0, 399, 0],
    }


def test_curve_series_marks_phases_and_samples():
    rows = build_curve_series(_curve_data())
    markers = {r["marker"] for r in rows if r["marker"]}
    assert {"CHARGE", "DRY", "FCs", "DROP"} <= markers
    # ~30s sampling across an 800s roast -> roughly 26 rows plus markers
    assert len(rows) >= 20
    # RoR appears once the ~30s lookback window is available
    assert rows[0]["ror"] is None
    assert any(r["ror"] is not None for r in rows)
    # Post-TP the synthetic curve climbs 0.4F per 2s sample = 12 F/min
    late = [r["ror"] for r in rows if r["ror"] is not None and r["time"] > 200]
    assert late and all(10 < v < 14 for v in late)


def test_curve_series_handles_missing_data():
    assert build_curve_series({"timex": [], "bt": [], "timeindex": []}) == []


# --- Prior-roast selection (cross-roast context for the LLM) ---

def _history_entry(rid, title, date, batch, notes=""):
    return {
        "roast_id": rid, "title": title, "roast_date": date, "batch_nr": batch,
        "metrics": {"total_time": 700, "drop_bt": 400 + batch},
        "next_roast": [f"advice-{batch}"],
        "cupping_notes": notes,
    }


def test_select_prior_roasts_same_bean_earlier_only():
    history = {
        "1_Bean_2026-06-01": _history_entry("1_Bean_2026-06-01", "Bean", "2026-06-01", 1, "flat"),
        "2_Other_2026-06-02": _history_entry("2_Other_2026-06-02", "Other", "2026-06-02", 2),
        "3_Bean_2026-06-03": _history_entry("3_Bean_2026-06-03", "Bean", "2026-06-03", 3),
        "5_Bean_2026-06-10": _history_entry("5_Bean_2026-06-10", "Bean", "2026-06-10", 5),
    }
    prior = select_prior_roasts(history, "5_Bean_2026-06-10", "Bean", "2026-06-10", 5)
    # Other bean excluded, current roast excluded, oldest first
    assert [p["batch_nr"] for p in prior] == [1, 3]
    assert prior[0]["cupping_notes"] == "flat"
    assert prior[0]["next_roast"] == ["advice-1"]
    # Nothing earlier than batch 1 -> no priors for it
    assert select_prior_roasts(history, "1_Bean_2026-06-01", "Bean", "2026-06-01", 1) == []


# --- Roast comparison ---

def test_compare_roasts_reports_raw_deltas():
    """With no target bands, compare reports the raw change and a descriptive
    direction (increased/decreased/unchanged) — no improved/regressed verdict."""
    a1 = {"metrics": {"dev_phase_time": 120, "drop_bt": 390}}
    a2 = {"metrics": {"dev_phase_time": 150, "drop_bt": 390}}
    changes = compare_roasts(a1, a2)
    devt = [c for c in changes if c["metric"] == "dev_phase_time"][0]
    assert devt["delta"] == 30
    assert devt["direction"] == "increased"
    drop = [c for c in changes if c["metric"] == "drop_bt"][0]
    assert drop["direction"] == "unchanged"


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


def test_cupping_regenerates_recommendations(tmp_path, monkeypatch):
    """Adding cupping notes re-runs the LLM with the notes — the flavor loop."""
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "roast", 1, "Test Bean", "2026-06-01", "10:00")
    analyze.cmd_scan(_Args(force=False))
    rid = list(analyze.load_history())[0]

    captured = {}

    def fake_llm(metrics, data, bean_profile=None, **kwargs):
        captured.update(kwargs)
        return {
            "recommendations": [
                {"priority": 1, "category": "Flavor Goal", "text": "post-cupping rec"},
            ],
            "next_roast": ["do X"],
        }, "ok"

    monkeypatch.setattr(roast_analysis, "generate_llm_recommendations", fake_llm)
    analyze.cmd_cupping(_Args(roast_id=rid, notes="tastes baked, needs more dev"))

    history = analyze.load_history()
    assert history[rid]["cupping_notes"] == "tastes baked, needs more dev"
    assert history[rid]["recommendations"][0]["text"] == "post-cupping rec"
    assert history[rid]["next_roast"] == ["do X"]
    assert history[rid]["llm_status"] == "ok"
    assert captured["cupping_notes"] == "tastes baked, needs more dev"


def test_cupping_keeps_old_recs_when_llm_unavailable(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "roast", 1, "Test Bean", "2026-06-01", "10:00")
    analyze.cmd_scan(_Args(force=False))
    history = analyze.load_history()
    rid = list(history)[0]
    history[rid]["recommendations"] = [{"priority": 1, "category": "X", "text": "old rec"}]
    analyze.save_history(history)

    # LLM still stubbed to fail (test-stub) — notes save, recs stay
    analyze.cmd_cupping(_Args(roast_id=rid, notes="fruity"))
    history = analyze.load_history()
    assert history[rid]["cupping_notes"] == "fruity"
    assert history[rid]["recommendations"][0]["text"] == "old rec"


def test_scan_passes_notes_and_priors_to_llm(tmp_path, monkeypatch):
    """A --force re-scan feeds preserved cupping notes and same-bean history
    into the LLM prompt instead of only restoring them afterwards."""
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "a_first", 1, "Test Bean", "2026-06-01", "10:00")
    _write_alog(logs, "b_second", 2, "Test Bean", "2026-06-08", "10:00")
    analyze.cmd_scan(_Args(force=False))

    history = analyze.load_history()
    rid2 = [r for r in history if history[r]["batch_nr"] == 2][0]
    history[rid2]["cupping_notes"] = "bright but thin"
    analyze.save_history(history)

    calls = []

    def fake_llm(metrics, data, bean_profile=None, **kwargs):
        calls.append({"batch": data.get("batch_nr"), **kwargs})
        return None, "test-stub"

    monkeypatch.setattr(roast_analysis, "generate_llm_recommendations", fake_llm)
    analyze.cmd_scan(_Args(force=True))

    batch2_call = [c for c in calls if c["batch"] == 2][0]
    assert batch2_call["cupping_notes"] == "bright but thin"
    assert [p["batch_nr"] for p in batch2_call["prior_roasts"]] == [1]
    batch1_call = [c for c in calls if c["batch"] == 1][0]
    assert batch1_call["prior_roasts"] == []


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


# --- Structured cupping intake ---

from cupping_intake import normalize_intake, intake_to_text, run_intake
from roast_plan import build_contrast_plan, parse_dev_times


def test_normalize_intake_accepts_values_numbers_and_labels():
    intake = normalize_intake({
        "brew": "esp",            # label prefix (espresso)
        "rest_days": "8",         # free integer as text
        "balance": "4",           # menu number -> +1 (a little roasty)
        "astringency": 0,         # exact value
        "preference": "better",
        "drink_again": "1",       # menu number -> yes
        "bogus": "ignored",
    })
    assert intake == {
        "brew": "espresso", "rest_days": 8, "balance": 1, "astringency": 0,
        "preference": "better", "drink_again": "yes",
    }


def test_normalize_intake_rejects_out_of_range():
    import pytest
    with pytest.raises(ValueError):
        normalize_intake({"balance": 7})
    with pytest.raises(ValueError):
        normalize_intake({})


def test_intake_to_text_reads_as_a_sentence():
    text = intake_to_text({"brew": "espresso", "rest_days": 8, "balance": -1,
                           "astringency": 3, "sweetness": 1, "preference": "worse",
                           "drink_again": "no", "notes": "sweet potato"})
    assert "espresso 8 days after roast" in text
    assert "a little sour" in text and "aspirin" in text
    assert "Versus previous batch: worse" in text
    assert text.endswith("Notes: sweet potato")


def test_run_intake_reprompts_and_skips_blanks():
    answers = iter(["1", "", "9", "3", "", "", "", "", "", ""])  # "9" invalid -> re-ask
    said = []
    intake = run_intake(ask=lambda _: next(answers), say=said.append)
    assert intake == {"brew": "switch", "balance": 0}  # menu 1 = Hario Switch
    assert any("not one of" in s for s in said)


def test_cupping_intake_json_stores_structured_and_text(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "b1", 1, "Bolivia", "2026-08-24", "16:08")
    analyze.cmd_scan(_Args(force=False))
    rid = "1_Bolivia_2026-08-24"

    captured = {}

    def fake_llm(metrics, data, bean_profile=None, **kw):
        captured.update(kw)
        return {"recommendations": [], "next_roast": ["shorten dev"]}, "ok"

    monkeypatch.setattr(roast_analysis, "generate_llm_recommendations", fake_llm)
    analyze.cmd_cupping(_Args(
        roast_id=rid, notes="a bit thin",
        intake_json=json.dumps({"balance": -1, "preference": "better", "drink_again": "yes"}),
        intake=False,
    ))
    history = json.loads((tmp_path / "history.json").read_text())
    assert history[rid]["cupping_intake"]["balance"] == -1
    assert history[rid]["cupping_intake"]["notes"] == "a bit thin"
    assert "a little sour" in history[rid]["cupping_notes"]
    # The rendered text is what the LLM sees
    assert captured["cupping_notes"] == history[rid]["cupping_notes"]
    assert history[rid]["next_roast"] == ["shorten dev"]

    # A --force re-scan keeps the structured answers, not just the text
    analyze.cmd_scan(_Args(force=True))
    history = json.loads((tmp_path / "history.json").read_text())
    assert history[rid]["cupping_intake"]["preference"] == "better"


# --- Contrast planner ---

def test_parse_dev_times():
    import pytest
    assert parse_dev_times("90,150,210") == (90, 150, 210)
    with pytest.raises(ValueError):
        parse_dev_times("90,abc")
    with pytest.raises(ValueError):
        parse_dev_times("0")


def test_contrast_plan_projects_drop_and_flags_eject(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "b1", 1, "Bolivia", "2026-08-24", "16:08")
    _write_alog(logs, "b2", 2, "Bolivia", "2026-08-24", "16:36")
    analyze.cmd_scan(_Args(force=False))
    history = json.loads((tmp_path / "history.json").read_text())

    plan = build_contrast_plan(history, "2_Bolivia_2026-08-24", dev_times=(90, 150, 600))
    assert plan["bean"] == "Bolivia"
    assert plan["fc_stats"]["count"] == 2
    labels = [c["label"] for c in plan["contrasts"]]
    assert labels[0].startswith("short") and labels[1].startswith("control") and labels[2].startswith("long")
    # Synthetic roast: FC 360F, drop 380F after 105s -> ~11.4 F/min post-FC rise
    short, control, long = plan["contrasts"]
    assert short["projected_drop_bt"] < control["projected_drop_bt"] < long["projected_drop_bt"]
    assert not control["unsafe"] and long["unsafe"]
    assert any("safety eject" in n for n in plan["notes"])
    # Rendered plan is printable and mentions every batch
    from roast_display import display_contrast_plan
    text = display_contrast_plan(plan)
    assert "FC +  90s" in text and "FC + 600s" in text and "near eject" in text


def test_contrast_plan_survives_missing_source(tmp_path, monkeypatch):
    history = {"x": {"roast_id": "x", "title": "Bean", "batch_nr": 1,
                     "roast_date": "2026-01-01", "metrics": {}}}
    plan = build_contrast_plan(history, "x")
    assert plan["base_schedule"]["moves"] == []
    assert any("Base schedule unavailable" in n for n in plan["notes"])
    assert len(plan["contrasts"]) == 3
