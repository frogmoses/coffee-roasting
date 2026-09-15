"""Profile designer with the model stubbed: valid design, repair, fallback, revision."""

import json

import profile_designer as pd
import roast_schedule as rs


def _curve():
    rows = [{"time": 0, "bt": 300.0}, {"time": 30, "bt": 220.0},
            {"time": 60, "bt": 180.0}, {"time": 90, "bt": 172.0}]
    for t in range(120, 721, 30):
        rows.append({"time": t, "bt": round(172.0 + (t - 90) * 0.34, 1)})
    return rows


def _envelope(borrowed=None):
    return {
        "bean": "Rwanda Test", "count": 4, "source_roasts": ["a", "b"],
        "median_curve": _curve(), "tp_time": 90.0, "tp_bt": 172.0, "dry_time": 380.0,
        "charge_bt": 300.0, "ror_at_fc": 12.0, "heat_soak_shift": -20.0,
        "staircase": {"start": {"heater": 100, "fan": 0}, "moves": [
            {"control": "fan", "value": 10, "median_time": 15.0, "median_bt": 236.0, "n": 4},
            {"control": "fan", "value": 20, "median_time": 90.0, "median_bt": 176.0, "n": 4},
            {"control": "heater", "value": 80, "median_time": 268.0, "median_bt": 262.0, "n": 4},
            {"control": "heater", "value": 60, "median_time": 388.0, "median_bt": 303.0, "n": 4},
        ]},
        "fc_stats": {"count": 4, "fc_time_mean": 650.0, "fc_time_sd": 20.0,
                     "fc_time_min": 620.0, "fc_time_max": 680.0, "fc_bt_mean": 362.0,
                     "post_fc_rise": 8.0, "post_fc_rise_measured": True},
        "ambient": [{"date": "2026-09-13", "batch": 24, "ambient_temp": 69.0, "fc_time": 684}],
        "dev_history": [
            {"batch": 22, "date": "2026-09-04", "dev_s": 70, "cupped": True, "drink_again": "no",
             "preference": "worse", "balance": -2, "notes": "worst"},
            {"batch": 23, "date": "2026-09-04", "dev_s": 134, "cupped": True, "drink_again": "meh",
             "preference": "better", "balance": 0, "notes": "best of the 3"},
        ],
        "ear_latency_s": 18.0, "ear_latency_n": 3, "borrowed_from": borrowed,
    }


def _dossier():
    return {"name": "Rwanda Test", "attributes": "City to Full City+.", "target_level": "City+",
            "roast_range": {"min": "City", "max": "Full City+"}, "process": "Wet Process",
            "cupping_notes": "Sweet and clean.", "flavor_scores": {"sugar": 8}}


def _design(drop=140, first_cut_bt=262, extra_moves=(), changes=None):
    """A model answer in the designer's JSON shape."""
    moves = [
        {"id": "fan10", "trigger": {"kind": "time", "bt": 0, "after": "CHARGE", "seconds": 15},
         "action": "fan", "value": 10, "why": "chaff"},
        {"id": "fan20", "trigger": {"kind": "time", "bt": 0, "after": "CHARGE", "seconds": 90},
         "action": "fan", "value": 20, "why": "convection"},
        {"id": "h80", "trigger": {"kind": "bt", "bt": first_cut_bt, "after": "CHARGE", "seconds": 0},
         "action": "heater", "value": 80, "why": "first cut"},
        {"id": "h60", "trigger": {"kind": "bt", "bt": 303, "after": "CHARGE", "seconds": 0},
         "action": "heater", "value": 60, "why": "into FC"},
    ] + list(extra_moves)
    return {
        "schedule": {"charge": {"bt": 300, "heater": 100, "fan": 0, "why": ""},
                     "moves": moves, "post_fc": [], "drop_seconds": drop},
        "predicted": {"fc_time_s": 650, "fc_bt": 362, "drop_bt": 381, "total_time_s": 790,
                      "confidence": "measured"},
        "roast_level_target": "City+",
        "flavor_hypothesis": "balance 0, astringency 1, sweetness 2",
        "contrast_lever": {"key": "dev_seconds", "values": [110, 140, 170], "why": "bracket"},
        "watch_for": ["a crash after FC"],
        "changes_from_baseline": changes if changes is not None else
        [{"what": "dev", "from": "134", "to": str(drop), "why": "the best cup was a touch sour"}],
        "rationale": "keep the staircase, trim dev",
    }


def _stub(answers):
    """A structured_call stand-in that replays canned designs and records prompts."""
    calls = []

    def call(system, content, schema, **kw):
        calls.append({"system": system, "content": content, "schema": schema})
        answer = answers.pop(0)
        if isinstance(answer, tuple):
            return answer
        return answer, "ok"
    call.calls = calls
    return call


def test_design_profile_returns_a_validated_schedule_and_prompt_has_context():
    call = _stub([_design()])
    result, status = pd.design_profile(_dossier(), _envelope(), call=call)
    assert status == "ok" and result["repair_rounds"] == 0
    sched = result["schedule"]
    assert sched["drop"] == {"after": "FCs", "seconds": 140}
    assert sched["moves"][2]["trigger"] == {"bt": 262} and "seconds" in sched["moves"][2]["expect"]
    assert sched["charge"]["warmup"]["preheat_bt"] == 370         # inherited from the baseline
    assert result["contrast_lever"]["key"] == "dev_seconds"
    assert result["validator"]["errors"] == []
    prompt = call.calls[0]["content"]
    assert "BEAN DOSSIER" in prompt and "Supplier roast range: City to Full City+" in prompt
    assert "MACHINE ENVELOPE" in prompt and '"heat_soak_shift": -20.0' in prompt
    assert "BASELINE SCHEDULE" in prompt and "H80@262F H60@303F DROP@FC+134s" in prompt  # best-cupped dev
    assert "Design version 1" in prompt and "City+" in prompt
    system = call.calls[0]["system"]
    assert "Schedule rules" in system and "Hario Switch" in system and "60-70 s of dead time" in system
    assert call.calls[0]["schema"] is pd._DESIGN_SCHEMA


def test_design_profile_repairs_once_then_gives_up():
    bad = _design(drop=240)                       # over the dev cap
    good = _design(drop=150)
    call = _stub([bad, good])
    result, status = pd.design_profile(_dossier(), _envelope(), call=call)
    assert status == "ok" and result["repair_rounds"] == 1
    assert "REJECTED BY THE VALIDATOR" in call.calls[1]["content"] and "outside" in call.calls[1]["content"]
    call = _stub([_design(drop=240), _design(drop=240)])
    result, status = pd.design_profile(_dossier(), _envelope(), call=call)
    assert result is None and status.startswith("design invalid after repair")


def test_design_profile_fails_soft_on_api_problems():
    call = _stub([(None, "no API credentials (set ANTHROPIC_API_KEY via the wrapper)")])
    result, status = pd.design_profile(_dossier(), _envelope(), call=call)
    assert result is None and "no API credentials" in status


def test_first_design_is_bounded_against_the_staircase():
    # six small changes are fine for a first design, seven are not
    extra = [{"id": "h90", "trigger": {"kind": "bt", "bt": 240, "after": "CHARGE", "seconds": 0},
              "action": "heater", "value": 90, "why": "gentler start"}]
    design = _design(drop=150, first_cut_bt=270, extra_moves=extra)
    design["schedule"]["moves"] = [design["schedule"]["moves"][i] for i in (0, 1, 4, 2, 3)]
    design["schedule"]["moves"][0]["value"] = 20
    call = _stub([design, design])
    result, status = pd.design_profile(_dossier(), _envelope(), call=call)
    assert status == "ok", status
    kinds = sorted(c["kind"] for c in result["validator_changes"])
    assert kinds == ["added", "bt", "dev", "value"]


def test_borrowed_envelope_is_flagged_in_the_prompt():
    call = _stub([_design()])
    pd.design_profile(_dossier(), _envelope(borrowed="Bolivia Caranavi"), call=call)
    assert "borrowed from Bolivia Caranavi" in call.calls[0]["content"]


def test_revise_profile_uses_the_reference_and_verdicts():
    base_result, _ = pd.design_profile(_dossier(), _envelope(), call=_stub([_design(drop=140)]))
    profile = {"version": 1, "schedule": base_result["schedule"], "status": "cupped",
               "predicted": base_result["predicted"], "flavor_hypothesis": "x",
               "changes_from_baseline": [], "roasts": [
                   {"batch_nr": 27, "roast_date": "2026-09-20", "dev_s": 141, "fc_time": 655,
                    "fc_bt": 361, "drop_bt": 380, "execution": "4/4 moves executed",
                    "cupping_intake": {"balance": -1, "astringency": 1, "drink_again": "yes"}}]}
    verdicts = [{"batch": 27, "balance": -1, "astringency": 1, "drink_again": "yes"}]
    call = _stub([_design(drop=160, changes=[{"what": "dev", "from": "140", "to": "160", "why": "a touch sour"}])])
    result, status = pd.revise_profile(profile, _dossier(), _envelope(), verdicts,
                                       prior_versions=[profile], call=call)
    assert status == "ok"
    assert result["schedule"]["drop"]["seconds"] == 160
    assert [c["kind"] for c in result["validator_changes"]] == ["dev"]
    prompt = call.calls[0]["content"]
    assert "CUPPING VERDICTS" in prompt and '"balance": -1' in prompt
    assert "PROFILE VERSIONS SO FAR" in prompt and "execution: 4/4 moves executed" in prompt
    assert "Revise from v1" in prompt
    # a revision that changes too much is refused even after repair
    wild = _design(drop=160, first_cut_bt=290)
    wild["schedule"]["moves"][0]["value"] = 30
    wild["schedule"]["moves"][1]["value"] = 40
    result, status = pd.revise_profile(profile, _dossier(), _envelope(), verdicts, call=_stub([wild, wild]))
    assert result is None and "at most 3" in status


def test_best_dev_prefers_drink_again_and_balance():
    assert pd._best_dev(_envelope()) == 134
    assert pd._best_dev({"dev_history": []}) is None
    json.dumps(pd._DESIGN_SCHEMA)          # schema is plain JSON
