"""Schedule shape, validator, timeline conversion, execution comparison."""

import copy
import json

import pytest

import roast_schedule as rs


def _curve():
    """A hot-charge curve: 300F falling to a 172F turning point at 90 s,
    then rising ~0.34 F/s to ~366F at 660 s."""
    rows = [{"time": 0, "bt": 300.0}, {"time": 30, "bt": 220.0},
            {"time": 60, "bt": 180.0}, {"time": 90, "bt": 172.0}]
    for t in range(120, 721, 30):
        rows.append({"time": t, "bt": round(172.0 + (t - 90) * 0.34, 1)})
    return rows


def _envelope():
    return {
        "median_curve": _curve(), "tp_time": 90.0, "tp_bt": 172.0, "dry_time": 380.0,
        "fc_stats": {"count": 4, "fc_time_mean": 650.0, "fc_time_sd": 20.0,
                     "fc_time_min": 620.0, "fc_time_max": 680.0, "fc_bt_mean": 362.0,
                     "post_fc_rise": 8.0, "post_fc_rise_measured": True},
        "borrowed_from": None,
    }


def _schedule(dev=150):
    """The roaster's real staircase as a schedule."""
    return rs.new_schedule(
        charge={"bt": 300, "heater": 100, "fan": 0},
        moves=[
            {"id": "fan10", "trigger": {"after": "CHARGE", "seconds": 15}, "action": "fan", "value": 10},
            {"id": "fan20", "trigger": {"after": "CHARGE", "seconds": 90}, "action": "fan", "value": 20},
            {"id": "h80", "trigger": {"bt": 262}, "action": "heater", "value": 80, "why": "first cut"},
            {"id": "h60", "trigger": {"bt": 303}, "action": "heater", "value": 60, "why": "into FC"},
        ],
        drop_s=dev,
    )


def _timeline():
    """A logged roast executing that staircase (with the 0:00 CHARGE row)."""
    return {
        "start_heater": 100, "start_fan": 0,
        "moves": [
            {"rel_time": 0.0, "bt": 300.0, "control": "Heater", "percentage": 100, "marker": "CHARGE"},
            {"rel_time": 13.0, "bt": 234.0, "control": "Fan", "percentage": 10, "marker": ""},
            {"rel_time": 89.0, "bt": 178.0, "control": "Fan", "percentage": 20, "marker": ""},
            {"rel_time": 267.0, "bt": 266.0, "control": "Heater", "percentage": 80, "marker": ""},
            {"rel_time": 387.0, "bt": 306.0, "control": "Heater", "percentage": 60, "marker": "DRY"},
        ],
        "phase_marks": {},
    }


def test_curve_helpers_round_trip():
    curve = _curve()
    t = rs.time_at_bt(curve, 262)
    assert 350 < t < 380
    assert abs(rs.bt_at_time(curve, t) - 262) < 0.5
    assert rs.time_at_bt(curve, 250) > 90          # never the descent
    assert rs.time_at_bt(curve, 500) is None
    assert rs.bt_at_time(curve, 0) == 300.0
    assert rs.tp_index(curve) == 3


def test_schedule_from_timeline_splits_by_tp_and_fc():
    tl = _timeline()
    tl["moves"].append({"rel_time": 700.0, "bt": 370.0, "control": "Fan", "percentage": 40, "marker": ""})
    sched = rs.schedule_from_timeline(tl, fc_time=650.0, envelope=_envelope(), dev_s=134)
    assert sched["charge"]["heater"] == 100 and sched["charge"]["fan"] == 0
    kinds = [rs.trigger_kind(m["trigger"]) for m in sched["moves"]]
    assert kinds == ["time", "time", "bt", "bt"]
    assert sched["moves"][0]["trigger"] == {"after": "CHARGE", "seconds": 13}
    assert sched["moves"][2]["trigger"] == {"bt": 266}
    assert sched["moves"][2]["expect"] == {"after": "CHARGE", "seconds": 267}
    assert sched["post_fc"] == [dict(sched["post_fc"][0], trigger={"after": "FCs", "seconds": 50})]
    assert sched["drop"] == {"after": "FCs", "seconds": 134}
    assert sched["predicted"]["fc_time_s"] == 650
    assert sched["predicted"]["drop_bt"] == round(362 + 8.0 * 134 / 60)


def test_validate_accepts_the_real_staircase_and_fills_expectations():
    res = rs.validate_schedule(_schedule(), _envelope())
    assert res["ok"], res["errors"]
    moves = res["schedule"]["moves"]
    assert moves[0]["expect"]["bt"] > 200                       # time move got a BT twin
    assert 350 < moves[2]["expect"]["seconds"] < 380            # BT move got a time twin
    assert res["schedule"]["guards"]["bt_ceiling"] == 398
    assert res["clamped"] == []


def test_validate_snaps_to_the_grid_and_records_it():
    sched = _schedule()
    sched["moves"][2]["value"] = 84
    res = rs.validate_schedule(sched, _envelope())
    assert res["ok"]
    assert res["schedule"]["moves"][2]["value"] == 80
    assert any("84 -> 80" in c for c in res["clamped"])


@pytest.mark.parametrize("mutate, needle", [
    (lambda s: s["moves"].append({"id": "h90", "trigger": {"bt": 340}, "action": "heater", "value": 90}), "rises"),
    (lambda s: s["moves"].insert(3, {"id": "h70", "trigger": {"bt": 265}, "action": "heater", "value": 70}), "one move, not two"),
    (lambda s: s["moves"][2].update(value=50), "more than 30"),
    (lambda s: s["moves"][1].update(value=70), "stalls"),
    (lambda s: s["drop"].update(seconds=200), "outside"),
    (lambda s: s["drop"].update(seconds=30), "outside"),
    (lambda s: s["moves"][1].update(trigger={"bt": 185}), "turning point"),
    (lambda s: s["moves"][3].update(trigger={"bt": 365}), "past first crack"),
    (lambda s: s["moves"][3].update(value=30), "keep at least"),
    (lambda s: s["moves"][2].update(action="drum"), "heater or fan"),
    (lambda s: s["post_fc"].append({"trigger": {"after": "FCs", "seconds": 10}, "action": "heater", "value": 70}), "rises after FC"),
    (lambda s: s["drop"].update(after="DRY"), "timed from FCs"),
])
def test_validate_rejects(mutate, needle):
    sched = _schedule()
    mutate(sched)
    res = rs.validate_schedule(sched, _envelope())
    assert not res["ok"]
    assert any(needle in e for e in res["errors"]), res["errors"]


def test_validate_projects_the_drop_against_the_ceiling():
    env = _envelope()
    env["fc_stats"]["post_fc_rise"] = 20.0        # 362 + 20*3 = 422F
    res = rs.validate_schedule(_schedule(dev=180), env)
    assert not res["ok"] and any("ceiling" in e for e in res["errors"])


def test_validate_warns_on_lone_10_percent_step_and_missing_curve():
    sched = _schedule()
    sched["moves"][2]["value"] = 90
    sched["moves"][3]["value"] = 80
    sched["moves"][3]["trigger"] = {"bt": 278}      # ~47 s after the 262F cut
    res = rs.validate_schedule(sched, _envelope())
    assert res["ok"] and any("10% step" in w for w in res["warnings"])
    assert any("sees a ramp" in w for w in res["warnings"])
    res2 = rs.validate_schedule(_schedule(), {})
    assert res2["ok"] and any("envelope" in w for w in res2["warnings"])


def test_revision_bounds_limit_change_count_and_size():
    base = _schedule()
    # one change inside the bound: fine
    sched = copy.deepcopy(base)
    sched["moves"][2]["trigger"] = {"bt": 275}
    res = rs.validate_schedule(sched, _envelope(), baseline=base)
    assert res["ok"], res["errors"]
    assert [c["kind"] for c in res["schedule"]["changes_from_baseline"]] == ["bt"]
    # one change outside it
    sched["moves"][2]["trigger"] = {"bt": 290}
    res = rs.validate_schedule(sched, _envelope(), baseline=base)
    assert any("bound" in e for e in res["errors"])
    # too many changes at once
    sched = copy.deepcopy(base)
    sched["moves"][0]["value"] = 20
    sched["moves"][1]["value"] = 30
    sched["moves"][2]["trigger"] = {"bt": 270}
    sched["drop"]["seconds"] = 160
    res = rs.validate_schedule(sched, _envelope(), baseline=base)
    assert any("at most 3" in e for e in res["errors"])


def test_diff_schedules_names_the_change_kinds():
    base = _schedule()
    new = copy.deepcopy(base)
    new["moves"].pop()                                  # removed a heater move
    new["moves"][0]["trigger"] = {"after": "CHARGE", "seconds": 30}
    new["drop"]["seconds"] = 120
    kinds = sorted(c["kind"] for c in rs.diff_schedules(new, base))
    assert kinds == ["dev", "removed", "seconds"]


def test_compare_execution_reports_deltas_missed_and_extra():
    sched = rs.validate_schedule(_schedule(), _envelope())["schedule"]
    tl = _timeline()
    tl["moves"].append({"rel_time": 500.0, "bt": 330.0, "control": "Fan", "percentage": 30, "marker": ""})
    tl["moves"] = [m for m in tl["moves"] if not (m["control"] == "Fan" and m["percentage"] == 20)]
    metrics = {"fc_time": 660.0, "fc_bt": 361.0, "drop_bt": 384.0, "dev_phase_time": 152.0}
    res = rs.compare_execution(sched, tl, metrics, _envelope())
    by_id = {r["id"]: r for r in res["moves"]}
    assert by_id["fan20"]["missed"] and res["missed"] == 1
    assert by_id["fan10"]["delta_s"] == -2
    assert by_id["h80"]["executed_bt"] == 266.0 and by_id["h80"]["delta_f"] == 4
    assert res["extra"] == [{"action": "fan", "value": 30, "at_s": 500.0, "bt": 330.0}]
    assert res["dev"]["delta_s"] == 2
    assert res["fc"]["actual_s"] == 660.0
    assert "3/4 moves executed" in res["summary"] and "1 unplanned" in res["summary"]


def test_schedule_rows_and_summary_render():
    sched = rs.validate_schedule(_schedule(), _envelope())["schedule"]
    rows = rs.schedule_rows(sched, _envelope())
    assert rows[0]["phase"] == "charge" and rows[-1]["phase"] == "drop"
    assert rows[3]["text"].startswith("Heater -> 80% at BT 262F")
    assert rows[-1]["when"] == "FC+2:30"
    assert rs.staircase_summary(sched) == "charge H100/F0 F10@0:15 F20@1:30 H80@262F H60@303F DROP@FC+150s"
    json.dumps(sched)                                       # JSON-clean
