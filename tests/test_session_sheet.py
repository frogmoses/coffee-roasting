"""Session sheet: batches from the contrast lever and the rendered page."""

import alarm_export as ae
import roast_schedule as rs
import session_sheet as ss


def _curve():
    rows = [{"time": 0, "bt": 300.0}, {"time": 30, "bt": 220.0},
            {"time": 60, "bt": 180.0}, {"time": 90, "bt": 172.0}]
    for t in range(120, 721, 30):
        rows.append({"time": t, "bt": round(172.0 + (t - 90) * 0.34, 1)})
    return rows


def _envelope():
    return {"median_curve": _curve(), "tp_time": 90.0, "tp_bt": 172.0, "dry_time": 380.0,
            "ear_latency_s": 18.0, "borrowed_from": None,
            "fc_stats": {"count": 4, "fc_time_mean": 650.0, "fc_time_sd": 20.0,
                         "fc_time_min": 620.0, "fc_time_max": 680.0, "fc_bt_mean": 362.0,
                         "post_fc_rise": 8.0, "post_fc_rise_measured": True}}


def _profile(exported=None):
    sched = rs.new_schedule(
        moves=[{"id": "fan10", "trigger": {"after": "CHARGE", "seconds": 15}, "action": "fan", "value": 10, "why": "chaff"},
               {"id": "h80", "trigger": {"bt": 262}, "action": "heater", "value": 80, "why": "first cut"},
               {"id": "h60", "trigger": {"bt": 303}, "action": "heater", "value": 60, "why": "into FC"}],
        drop_s=140)
    sched = rs.validate_schedule(sched, _envelope())["schedule"]
    return {"slug": "rwanda-test", "bean": "Rwanda Test", "version": 2, "schedule": sched,
            "predicted": {"fc_time_s": 650, "fc_bt": 362, "drop_bt": 381, "confidence": "measured"},
            "flavor_hypothesis": "balanced, a hint of sweetness", "rationale": "Keep the staircase, trim dev.",
            "contrast_lever": {"key": "dev_seconds", "values": [110, 140, 170], "why": "bracket the design"},
            "watch_for": ["a crash after FC"], "validator": {"warnings": ["a lone 10% step"]},
            "based_on": {"kind": "design"}, "exported": exported, "roasts": []}


def test_batches_put_the_design_first_then_short_then_long():
    cards = ss.batches_from_profile(_profile(), _envelope())
    assert [(c["order"], c["label"], c["dev_s"]) for c in cards] == \
           [(1, "planned", 140), (2, "shorter", 110), (3, "longer", 170)]
    assert cards[0]["drop_bt"] == round(362 + 8.0 * 140 / 60)
    assert all(c["margin"] > 10 for c in cards)
    # a profile without a lever still gets its own batch
    prof = _profile()
    prof["contrast_lever"] = None
    assert [c["dev_s"] for c in ss.batches_from_profile(prof, _envelope())] == [140]


def test_render_sheet_manual_drop_first_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path))
    prof = _profile(exported={"at": "2026-09-14T12:00:00", "mode": "paired", "ear_latency_s": 18.0,
                              "manual_drop": True, "files": ["x/profile-v2.alrm"]})
    rows = ae.schedule_to_alarms(prof["schedule"], _envelope(), manual_drop=True)["rows"]
    html = ss.render_sheet(prof, {"name": "Rwanda Test"}, _envelope(), rows, alarm_file="profile-v2.alrm")
    assert "<title>Rwanda Test v2</title>" in html and "<style>" in html
    assert "Config &rarr; Alarms &rarr; Load <code>profile-v2.alrm</code>" in html
    assert "Do not press CONTROL yourself" in html and "DROP is the panic button" in html
    # charge on the way back up through 300F, never as BT first falls past it
    assert "Charge at the pop-up" in html and "climbs back through 300F" in html
    assert "Charge as soon as" not in html
    assert "COOL END" in html and "save the log" in html
    assert "You mark FCs" in html                                 # manual session: one marker
    assert "The alarms run the warm-up" in html and "370F" in html
    assert "Heater -&gt; 80% at BT 262F" in html and "bean temp" in html
    assert "Event Button 3 (Control On)" in html                  # the alarm table
    assert "Batch 1 &middot; planned" in html and "FC + 2:20" in html and "FC + 1:50" in html
    assert "press <strong>DROP at 2:20</strong>" in html           # manual drop, mm:ss
    assert "Hario Switch" in html and "Triangle first" in html and "Would you happily drink it again?" in html
    assert "profile &quot;Rwanda Test&quot; --revise" in html          # escaped in the <pre>
    assert "Note: a lone 10% step" in html
    path = ss.write_sheet("rwanda-test", 2, html)
    assert path == tmp_path / "rwanda-test" / "sheet-v2.html" and path.read_text() == html


def test_render_sheet_timed_drop_and_no_alarms():
    prof = _profile(exported={"at": "2026-09-14T12:00:00", "mode": "paired", "ear_latency_s": 18.0,
                              "manual_drop": False, "files": []})
    html = ss.render_sheet(prof, None, _envelope(), None)
    assert "The DROP alarm fires <strong>2:20</strong>" in html and "minus its 18 s latency" in html
    assert "keep your hands off the FCs button" in html          # ear session: one marker
    assert "Warm up by hand" in html and "row by row" not in html
    env = dict(_envelope(), borrowed_from="Bolivia Caranavi")
    html = ss.render_sheet(prof, None, env, None)
    assert "times come from your <b>Bolivia Caranavi</b> roasts" in html


def test_fan_after_fc_bracket_cards_keep_dev_and_name_the_slider_move():
    prof = _profile(exported={"at": "2026-09-14T12:00:00", "mode": "bt", "ear_latency_s": 0,
                              "manual_drop": True, "files": []})
    base = ss._fan_into_fc(prof["schedule"])                         # the fixture's last pre-FC fan
    prof["contrast_lever"] = {"key": "fan_after_fc", "values": [base, 40, 30], "why": "airflow from FC"}
    cards = ss.batches_from_profile(prof, _envelope())
    assert [c["label"] for c in cards] == ["planned", "fan 40%", "fan 30%"]
    assert {c["dev_s"] for c in cards} == {140}                      # development held fixed
    assert cards[0]["manual_move"] is None and cards[0]["fan_after_fc"] == base
    assert cards[1]["manual_move"] == "at your FCs mark, move the fan slider to 40%"
    html = ss.render_sheet(prof, None, _envelope(), None)
    assert "Batch 2 &middot; fan 40%" in html and "At FCs: fan &rarr; 40%" in html
    assert "move the fan slider to 30%</strong>, then start a stopwatch" in html
    assert "airflow from FC" in html
