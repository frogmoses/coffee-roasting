"""Schedule -> Artisan alarm rows -> .alrm / [Alarms] round trips."""

import json

from pathlib import Path

import alarm_export as ae
import roast_schedule as rs
from artisan_conf import ArtisanConf

REPO_CONF = Path(__file__).resolve().parents[1] / "artisan" / "Artisan.conf"


def _curve():
    rows = [{"time": 0, "bt": 300.0}, {"time": 30, "bt": 220.0},
            {"time": 60, "bt": 180.0}, {"time": 90, "bt": 172.0}]
    for t in range(120, 721, 30):
        rows.append({"time": t, "bt": round(172.0 + (t - 90) * 0.34, 1)})
    return rows


def _envelope():
    return {"median_curve": _curve(), "tp_time": 90.0, "tp_bt": 172.0, "dry_time": 380.0,
            "fc_stats": {"count": 4, "fc_time_mean": 650.0, "fc_time_sd": 20.0,
                         "fc_time_min": 620.0, "fc_time_max": 680.0, "fc_bt_mean": 362.0,
                         "post_fc_rise": 8.0, "post_fc_rise_measured": True}}


def _schedule():
    sched = rs.new_schedule(
        moves=[
            {"id": "fan10", "trigger": {"after": "CHARGE", "seconds": 15}, "action": "fan", "value": 10},
            {"id": "h80", "trigger": {"bt": 262}, "action": "heater", "value": 80},
            {"id": "h60", "trigger": {"bt": 303}, "action": "heater", "value": 60},
        ],
        post_fc=[{"trigger": {"after": "FCs", "seconds": 0}, "action": "fan", "value": 40}],
        drop_s=150,
    )
    return rs.validate_schedule(sched, _envelope())["schedule"]


def _by_label(rows, needle):
    hits = [(i, r) for i, r in enumerate(rows) if needle in r["label"]]
    assert len(hits) == 1, [r["label"] for r in rows]
    return hits[0]


def test_paired_rows_encode_bt_with_time_fallback_and_guards():
    res = ae.schedule_to_alarms(_schedule(), _envelope(), ear_latency_s=18)
    assert res["errors"] == [] and res["warnings"] == []
    rows = res["rows"]
    # markers
    i_roast, roast = _by_label(rows, "roast started")
    assert i_roast == 0 and roast["from"] == ae.FROM_CHARGE and roast["source"] == ae.SRC_BT
    assert roast["cond"] == ae.COND_GT and roast["temperature"] == 0 and roast["action"] == ae.ACT_NONE
    i_desc, desc = _by_label(rows, "past the turning point")
    assert desc["cond"] == ae.COND_LT and desc["temperature"] == 252     # lowest trigger - 10
    assert desc["offset"] == ae.DESCENT_FALLBACK_S == 45                  # late-CHARGE clock twin
    # warm-up: control first (the driver drops commands until control is on),
    # then the drum state buttons, then heater/fan
    i_ctrl, ctrl = _by_label(rows, "Hottop control on")
    assert ctrl["from"] == ae.FROM_ON and ctrl["offset"] == 1
    assert ctrl["action"] == ae.ACT_BUTTON and ctrl["string"] == "3" and ctrl["negguard"] == i_roast
    i_motor, motor = _by_label(rows, "drum motor on")
    assert motor["from"] == ae.FROM_ON and motor["offset"] == 3 and i_motor > i_ctrl
    assert motor["action"] == ae.ACT_BUTTON and motor["string"] == "1,5,8" and motor["negguard"] == i_roast
    i_pre, pre = _by_label(rows, "370F reached")
    assert pre["source"] == ae.SRC_BT and pre["temperature"] == 370 and pre["string"] == "0"
    # heater back on at 300F falling; the charge pop-up waits until BT has
    # fallen 5F further and climbed back through 300F
    i_back = next(i for i, r in enumerate(rows) if r["action"] == ae.ACT_HEATER and r["string"] == "100"
                  and r["cond"] == ae.COND_LT and r["temperature"] == 300)
    assert rows[i_back]["guard"] == i_pre
    i_turn, turn = _by_label(rows, "cooled below the charge temperature")
    assert turn["cond"] == ae.COND_LT and turn["temperature"] == 295 and turn["guard"] == i_back
    assert turn["action"] == ae.ACT_NONE and turn["negguard"] == i_roast
    i_pop, pop = _by_label(rows, "charge pop-up")
    assert pop["cond"] == ae.COND_GT and pop["temperature"] == 300 and pop["guard"] == i_turn
    assert pop["action"] == ae.ACT_POPUP and pop["beep"] == 1 and pop["negguard"] == i_roast
    assert i_pre < i_back < i_turn < i_pop
    # heater 100% and fan 0% at ON+5s are But-Not guarded by the preheat row
    # (a hot drum at ON must not have its cooling undone)
    on_rows = [r for r in rows if r["from"] == ae.FROM_ON and r["offset"] == 5]
    assert {r["action"] for r in on_rows} == {ae.ACT_HEATER, ae.ACT_FAN}
    assert all(r["negguard"] == i_pre for r in on_rows)
    # the missed-FC nag sits between the FC marker and the drop
    _, nag = _by_label(rows, "no FCs mark yet")
    assert nag["temperature"] == ae.FC_NAG_BT == 372 and nag["action"] == ae.ACT_POPUP and nag["beep"] == 1
    # staircase
    _, fan10 = _by_label(rows, "fan 10% at CHARGE + 0:15")
    assert fan10["from"] == ae.FROM_CHARGE and fan10["offset"] == 15 and fan10["source"] == ae.SRC_NONE
    assert fan10["action"] == ae.ACT_FAN and fan10["string"] == "10" and fan10["guard"] == -1
    _, h80 = _by_label(rows, "heater 80% at BT 262F or CHARGE")
    assert h80["source"] == ae.SRC_BT and h80["temperature"] == 262 and 350 < h80["offset"] < 380
    assert h80["guard"] == i_desc and h80["action"] == ae.ACT_HEATER and h80["string"] == "80"
    # FC helpers, post-FC, drop, ceiling, cool
    _, popup = _by_label(rows, "FC imminent")
    assert popup["temperature"] == 356 and popup["action"] == ae.ACT_POPUP and popup["beep"] == 1
    i_fc, fc = _by_label(rows, "first crack possible")
    assert fc["temperature"] == 350 and fc["action"] == ae.ACT_NONE
    _, fan40 = _by_label(rows, "fan 40% at FCs")
    assert fan40["from"] == ae.FROM_FCS and fan40["offset"] == 1 and fan40["guard"] == i_fc
    _, drop = _by_label(rows, "DROP at FCs + 2:12")
    assert drop["from"] == ae.FROM_FCS and drop["offset"] == 132 and drop["action"] == ae.ACT_DROP
    assert drop["guard"] == i_fc
    _, ceil = _by_label(rows, "safety ceiling")
    assert ceil["temperature"] == 398 and ceil["action"] == ae.ACT_DROP and ceil["guard"] == -1
    _, cool = _by_label(rows, "COOL END")
    assert cool["from"] == ae.FROM_DROP and cool["offset"] == 240 and cool["action"] == ae.ACT_COOL


def test_modes_and_manual_drop():
    bt_only = ae.schedule_to_alarms(_schedule(), _envelope(), mode="bt")["rows"]
    _, h80 = _by_label(bt_only, "heater 80% at BT 262F")
    assert h80["offset"] == 0 and h80["source"] == ae.SRC_BT
    time_only = ae.schedule_to_alarms(_schedule(), _envelope(), mode="time", warmup=False)["rows"]
    _, h80t = _by_label(time_only, "heater 80% at CHARGE")
    assert h80t["source"] == ae.SRC_NONE and h80t["offset"] > 0 and h80t["guard"] == -1
    assert not any("turning point" in r["label"] for r in time_only)
    assert not any(r["from"] == ae.FROM_ON for r in time_only)
    manual = ae.schedule_to_alarms(_schedule(), _envelope(), manual_drop=True, cool_after_s=0)["rows"]
    drops = [r for r in manual if r["action"] == ae.ACT_DROP]
    assert len(drops) == 1 and "ceiling" in drops[0]["label"]
    assert not any(r["action"] == ae.ACT_COOL for r in manual)


def test_descent_marker_needs_room_above_tp():
    sched = _schedule()
    env = _envelope()
    env["tp_bt"] = 258.0
    res = ae.schedule_to_alarms(sched, env)
    assert any("descent marker" in e for e in res["errors"])


def test_alrm_round_trip_uses_plural_keys(tmp_path):
    rows = ae.schedule_to_alarms(_schedule(), _envelope())["rows"]
    path = tmp_path / "rwanda-v1.alrm"
    ae.write_alrm(path, rows)
    data = json.loads(path.read_text())
    assert sorted(data) == sorted(ae.ALRM_KEYS)
    assert all(len(v) == len(rows) for v in data.values())
    assert all(isinstance(x, int) for x in data["alarmoffsets"] + data["alarmguards"])
    back = ae.read_alrm(path)
    assert [(r["from"], r["offset"], r["action"], r["string"]) for r in back] == \
           [(r["from"], r["offset"], r["action"], r["string"]) for r in rows]


def test_conf_round_trip_decodes_in_artisan_conf(tmp_path):
    rows = ae.schedule_to_alarms(_schedule(), _envelope(), ear_latency_s=18)["rows"]
    path = tmp_path / "Artisan.conf"
    path.write_text("[General]\nKeepON=false\n", encoding="utf-8")
    conf = ArtisanConf.load(path)
    ae.rows_to_conf(conf, rows)
    conf.save(path)
    decoded = ArtisanConf.load(path).alarms()
    assert len(decoded) == len(rows)
    buttons = [r for r in decoded if r["action"] == "Event Button"]
    assert [b["string"] for b in buttons] == ["3", "1,5,8"]
    assert all(b["from"] == "ON" and b["negguard"] == 0 for b in buttons)
    drop = next(r for r in decoded if r["action"] == "DROP" and r["from"] == "FC START")
    assert drop["offset_s"] == 132
    assert ArtisanConf.load(path).roundtrip_mismatches() == []
    import pytest
    with pytest.raises(ValueError):
        ae.rows_to_conf(conf, rows[:1])


def test_describe_rows_reads_like_the_dialog():
    rows = ae.schedule_to_alarms(_schedule(), _envelope(), ear_latency_s=18)["rows"]
    text = "\n".join(ae.describe_rows(rows))
    assert "ON +0:01" in text and "Event Button 3 (Control On)" in text
    assert "Event Button 1,5,8 (Motor On, Door Close, Stirrer Off)" in text
    assert "CHARGE BT < 252F or +0:45" in text
    assert "CHARGE BT > 262F or +" in text and "Heater 80%" in text
    assert "FCs +2:12" in text and "-> DROP *" in text
    assert "[unless #1]" in text and "[if #" in text


def test_button_numbers_match_the_roaster_settings_file():
    """The Event Button rows name custom buttons by 1-based slot; the slots
    must hold the commands the rows assume, in the repo copy of the
    roaster's settings (deployed with artisan/deploy.sh push)."""
    buttons = {b["index"]: b for b in ArtisanConf.load(REPO_CONF).custom_buttons()}
    expect = {ae.CONTROL_BUTTON: "control(1)", ae.MOTOR_BUTTON: "motor(1)",
              ae.DOOR_CLOSE_BUTTON: "solenoid(0)", ae.STIRRER_OFF_BUTTON: "stirrer(0)"}
    for slot, command in expect.items():
        assert buttons[slot]["action"] == "Hottop Command", slot
        assert buttons[slot]["command"] == command, slot
        assert buttons[slot]["visible"], slot
