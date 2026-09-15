"""CLI: profile --from-roast, export-alarms, and profile linking at scan."""

import json

import analyze
from test_analysis_and_cli import _write_alog, _patched_env, _Args


def _write_staircase_alog(directory, name, batch_nr, title, date, time_str):
    """A synthetic roast with logged heater/fan moves (the CLI test .alog
    has none), a 300F charge, a turning point, and FC at 600 s."""
    timex = [float(i * 2) for i in range(400)]
    bt = [300.0 - i * 4 for i in range(30)] + [180.0 + (i - 30) * 0.5 for i in range(30, 400)]
    raw = {
        "title": title, "roastbatchnr": batch_nr, "roastisodate": date, "roasttime": time_str,
        "roastUUID": f"uuid-{name}", "timex": timex, "temp2": bt, "temp1": [v + 40 for v in bt],
        "timeindex": [0, 150, 300, 0, 0, 0, 375, 0],
        "specialevents": [0, 8, 45, 135, 160, 190],
        "specialeventstype": [3, 0, 0, 3, 3, 3],
        "specialeventsvalue": [11, 2, 3, 10, 9, 7],
        "specialeventsStrings": ["", "", "", "", "", ""],
        "computed": {
            "totaltime": 750, "dryphasetime": 300, "midphasetime": 300, "finishphasetime": 150,
            "CHARGE_BT": 300.0, "TP_BT": 180.0, "TP_time": 60.0, "DRY_BT": 300.0,
            "FCs_BT": 360.0, "FCs_time": 600.0, "DROP_BT": 382.0, "DROP_time": 750.0, "fcs_ror": 12.0,
        },
    }
    path = directory / f"{name}.alog"
    path.write_text(repr(raw))
    return path


def _setup(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path / "beans"))
    _write_staircase_alog(logs, "r1", 1, "Rwanda Test", "2026-09-04", "09:35")
    _write_staircase_alog(logs, "r2", 2, "Rwanda Test", "2026-09-13", "10:43")
    analyze.cmd_scan(_Args(force=False))
    return logs


def test_profile_from_roast_reproduces_the_staircase(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    analyze.cmd_profile(_Args(bean="rwanda", from_roast="2", dev=None, version=None,
                              set_reference=None, roast=None))
    out = capsys.readouterr().out
    assert "Saved" in out and "Roast Profile: Rwanda Test  v1" in out
    prof = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v1.json").read_text())
    sched = prof["schedule"]
    assert sched["charge"]["heater"] == 100
    kinds = [(m["action"], m["value"], "bt" in m["trigger"]) for m in sched["moves"]]
    assert kinds == [("fan", 10, False), ("fan", 20, False), ("heater", 90, True),
                     ("heater", 80, True), ("heater", 60, True)]
    assert sched["drop"]["seconds"] == 150                      # the roast's own dev time
    assert prof["based_on"]["roast_id"] == "2_Rwanda Test_2026-09-13"
    assert prof["envelope_snapshot"]["fc_stats"]["count"] == 2
    # showing it again reads the saved version
    analyze.cmd_profile(_Args(bean="Rwanda Test", from_roast=None, dev=None, version=1,
                              set_reference=None, roast=None))
    assert "[draft]" in capsys.readouterr().out


def test_export_alarms_writes_alrm_and_marks_the_profile(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    analyze.cmd_profile(_Args(bean="rwanda", from_roast="2", dev=None, version=None,
                              set_reference=None, roast=None))
    capsys.readouterr()
    analyze.cmd_export_alarms(_Args(bean="rwanda", version=None, mode="paired", manual_drop=True,
                                    ear_latency="auto", no_warmup=False, cool=None, install_local=False))
    out = capsys.readouterr().out
    path = tmp_path / "beans" / "rwanda-test" / "alarms" / "profile-v1.alrm"
    assert path.exists() and "Written:" in out and "DROP is manual" in out
    data = json.loads(path.read_text())
    assert len(data["alarmflags"]) == len(data["alarmstrings"]) > 10
    assert "Heater 90%" in out and "Event Button 1 (Motor On)" in out
    prof = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v1.json").read_text())
    assert prof["status"] == "exported" and prof["exported"]["manual_drop"] is True
    assert prof["exported"]["files"] == [str(path)]
    # time-only export goes to its own file and needs no descent marker
    analyze.cmd_export_alarms(_Args(bean="rwanda", version=1, mode="time", manual_drop=False,
                                    ear_latency="10", no_warmup=True, cool=0, install_local=False))
    out = capsys.readouterr().out
    assert (tmp_path / "beans" / "rwanda-test" / "alarms" / "profile-v1-time.alrm").exists()
    assert "turning point" not in out and "FCs +2:20" in out          # 150 - 10 s latency


def test_scan_links_roasts_to_the_exported_profile(tmp_path, monkeypatch, capsys):
    logs = _setup(tmp_path, monkeypatch)
    analyze.cmd_profile(_Args(bean="rwanda", from_roast="2", dev=None, version=None,
                              set_reference=None, roast=None))
    analyze.cmd_export_alarms(_Args(bean="rwanda", version=None, mode="paired", manual_drop=True,
                                    ear_latency="auto", no_warmup=False, cool=None, install_local=False))
    capsys.readouterr()
    # a roast after the export (dates in the future relative to the export stamp)
    _write_staircase_alog(logs, "r3", 3, "Rwanda Test", "2099-01-01", "10:00")
    analyze.cmd_scan(_Args(force=False))
    out = capsys.readouterr().out
    assert "Profile v1: 5/5 moves executed" in out
    history = json.loads((tmp_path / "history.json").read_text())
    entry = history["3_Rwanda Test_2099-01-01"]
    assert entry["profile_ref"] == {"slug": "rwanda-test", "version": 1}
    assert entry["execution"]["missed"] == 0 and entry["control_timeline"]["moves"]
    prof = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v1.json").read_text())
    assert [r["batch_nr"] for r in prof["roasts"]] == [3] and prof["status"] == "roasted"
    # the earlier roasts (before the export) are not linked
    assert "profile_ref" not in history["2_Rwanda Test_2026-09-13"]
    # full shows the execution box
    analyze.cmd_full(_Args(roast_id="3", force=False, verbose=False))
    assert "Designed vs Executed" in capsys.readouterr().out


def test_set_reference(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    analyze.cmd_profile(_Args(bean="rwanda", from_roast="2", dev=None, version=None,
                              set_reference=None, roast=None))
    capsys.readouterr()
    analyze.cmd_profile(_Args(bean="rwanda", from_roast=None, dev=None, version=None,
                              set_reference=1, roast="2"))
    assert "Reference for Rwanda Test: v1" in capsys.readouterr().out
    ref = json.loads((tmp_path / "beans" / "rwanda-test" / "reference.json").read_text())
    assert ref["version"] == 1 and ref["roast_id"] == "2_Rwanda Test_2026-09-13"


def test_quantifier_warning_reads_the_settings_file(tmp_path, capsys):
    conf = tmp_path / "Artisan.conf"
    conf.write_text("[Quantifiers]\nquantifieractive=1, 0, 0, 1\n", encoding="utf-8")
    analyze._warn_if_quantifiers_active(conf)
    assert "event quantifiers are active" in capsys.readouterr().out
    conf.write_text("[Quantifiers]\nquantifieractive=0, 0, 0, 0\n", encoding="utf-8")
    analyze._warn_if_quantifiers_active(conf)
    assert capsys.readouterr().out == ""
    analyze._warn_if_quantifiers_active(tmp_path / "missing.conf")      # quiet when absent
    assert capsys.readouterr().out == ""
