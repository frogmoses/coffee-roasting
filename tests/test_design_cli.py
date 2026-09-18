"""CLI: dossier, profile --design / --revise, cupping verdicts, sheet."""

import json

import analyze
from test_analysis_and_cli import _patched_env, _Args
from test_profile_cli import _write_staircase_alog


def _setup(tmp_path, monkeypatch):
    logs = _patched_env(tmp_path, monkeypatch)
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path / "beans"))
    # no find-coffee, no supplier page, no model
    monkeypatch.setattr(analyze, "ensure_server_running", lambda: (False, "not configured"))
    monkeypatch.setattr(analyze, "stop_server", lambda: None)
    _write_staircase_alog(logs, "r1", 1, "Rwanda Test", "2026-09-04", "09:35")
    _write_staircase_alog(logs, "r2", 2, "Rwanda Test", "2026-09-13", "10:43")
    analyze.cmd_scan(_Args(force=False))
    return logs


def _design_answer(drop=140):
    """What a stubbed designer returns (already validated shape)."""
    from roast_schedule import new_schedule, validate_schedule
    sched = new_schedule(
        moves=[{"id": "fan10", "trigger": {"after": "CHARGE", "seconds": 15}, "action": "fan", "value": 10, "why": "chaff"},
               {"id": "fan20", "trigger": {"after": "CHARGE", "seconds": 90}, "action": "fan", "value": 20, "why": "airflow"},
               {"id": "h90", "trigger": {"bt": 270}, "action": "heater", "value": 90, "why": "ease"},
               {"id": "h80", "trigger": {"bt": 290}, "action": "heater", "value": 80, "why": "ease"},
               {"id": "h60", "trigger": {"bt": 320}, "action": "heater", "value": 60, "why": "into FC"}],
        drop_s=drop)
    return {"schedule": sched, "predicted": {"fc_time_s": 600, "fc_bt": 360, "drop_bt": 385,
                                             "total_time_s": 740, "confidence": "measured"},
            "roast_level_target": "City+", "flavor_hypothesis": "balanced and sweet",
            "contrast_lever": {"key": "dev_seconds", "values": [110, drop, 170], "why": "bracket"},
            "watch_for": ["crash"], "changes_from_baseline": [{"what": "dev", "from": "150", "to": str(drop), "why": "cup"}],
            "validator_changes": [{"what": "dev seconds", "kind": "dev", "from": 150, "to": drop}],
            "rationale": "trim dev", "validator": {"errors": [], "warnings": [], "clamped": []},
            "repair_rounds": 0}


def test_dossier_command_builds_and_stores_without_network(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    analyze.cmd_dossier(_Args(bean="rwanda", research=False, alias="Rwanda Test Lot 2", no_fetch=True))
    out = capsys.readouterr().out
    assert "Dossier for Rwanda Test (built (no page))" in out and "Target for this operator: City+" in out
    doc = json.loads((tmp_path / "beans" / "rwanda-test" / "dossier.json").read_text())
    assert doc["aliases"] == ["Rwanda Test Lot 2"] and doc["roast_range"] is None
    # a second run reads the stored dossier
    analyze.cmd_dossier(_Args(bean="rwanda", research=False, alias=None, no_fetch=True))
    assert "(stored)" in capsys.readouterr().out


def test_profile_design_saves_a_version_and_export_writes_the_sheet(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    seen = {}

    def fake_design(dossier, env, prefs=None, prior_versions=None, baseline=None, dev_s=None, call=None):
        seen["dossier"] = dossier
        seen["env"] = env
        seen["prior"] = prior_versions
        return _design_answer(), "ok"

    monkeypatch.setattr(analyze, "design_profile", fake_design)
    analyze.cmd_profile(_Args(bean="rwanda", design=True, revise=False, from_roast=None, dev=None,
                              version=None, set_reference=None, roast=None))
    out = capsys.readouterr().out
    assert "Designing with" in out and "Saved" in out and "Roast Profile: Rwanda Test  v1" in out
    assert seen["dossier"]["name"] == "Rwanda Test" and seen["env"]["count"] == 2 and seen["prior"] == []
    prof = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v1.json").read_text())
    assert prof["based_on"]["kind"] == "design" and prof["designer"]["status"] == "ok"
    assert prof["schedule"]["drop"]["seconds"] == 140 and prof["contrast_lever"]["values"] == [110, 140, 170]
    assert prof["flavor_hypothesis"] == "balanced and sweet"
    analyze.cmd_export_alarms(_Args(bean="rwanda", version=None, mode="paired", manual_drop=True,
                                    ear_latency="auto", no_warmup=False, cool=None, install_local=False))
    out = capsys.readouterr().out
    sheet = tmp_path / "beans" / "rwanda-test" / "sheet-v1.html"
    assert "Sheet:" in out and sheet.exists()
    html = sheet.read_text()
    assert "profile-v1.alrm" in html and "Batch 1 &middot; design" in html and "DROP at 2:20" in html
    # sheet command regenerates from the stored export settings
    sheet.unlink()
    analyze.cmd_sheet(_Args(bean="rwanda", version=None))
    assert sheet.exists() and "Event Button 3 (Control On)" in sheet.read_text()


def test_profile_design_falls_back_to_the_staircase(tmp_path, monkeypatch, capsys):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(analyze, "design_profile", lambda *a, **k: (None, "no API credentials"))
    analyze.cmd_profile(_Args(bean="rwanda", design=True, revise=False, from_roast=None, dev=None,
                              version=None, set_reference=None, roast=None))
    out = capsys.readouterr().out
    assert "designer unavailable: no API credentials" in out and "Saved" in out
    prof = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v1.json").read_text())
    assert prof["based_on"]["kind"] == "staircase"
    assert prof["validator"]["warnings"][0].startswith("designer unavailable")
    assert [m["value"] for m in prof["schedule"]["moves"]] == [10, 20, 90, 80, 60]


def test_cupping_attaches_verdict_and_revise_makes_the_next_version(tmp_path, monkeypatch, capsys):
    logs = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(analyze, "design_profile", lambda *a, **k: (_design_answer(), "ok"))
    analyze.cmd_profile(_Args(bean="rwanda", design=True, revise=False, from_roast=None, dev=None,
                              version=None, set_reference=None, roast=None))
    analyze.cmd_export_alarms(_Args(bean="rwanda", version=None, mode="paired", manual_drop=True,
                                    ear_latency="auto", no_warmup=False, cool=None, install_local=False))
    _write_staircase_alog(logs, "r3", 3, "Rwanda Test", "2099-01-01", "10:00")
    analyze.cmd_scan(_Args(force=False))
    capsys.readouterr()
    # cupping records the verdict on v1
    analyze.cmd_cupping(_Args(roast_id="3", notes=None, intake=False,
                              intake_json='{"balance": -1, "astringency": 1, "drink_again": "yes"}'))
    out = capsys.readouterr().out
    assert "Verdict recorded on profile v1 (1/1 of its roasts cupped)" in out and "--revise" in out
    prof = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v1.json").read_text())
    assert prof["roasts"][0]["cupping_intake"]["balance"] == -1 and prof["status"] == "cupped"
    # revise: the stub sees the verdicts and returns a longer dev
    seen = {}

    def fake_revise(profile, dossier, env, verdicts, prefs=None, prior_versions=None, reference=None, call=None):
        seen["verdicts"] = verdicts
        seen["reference"] = reference
        return _design_answer(drop=160), "ok"

    monkeypatch.setattr(analyze, "revise_profile", fake_revise)
    analyze.cmd_profile(_Args(bean="rwanda", design=False, revise=True, from_roast=None, dev=None,
                              version=None, set_reference=None, roast=None))
    out = capsys.readouterr().out
    assert "Revising v1 from 1 verdict(s)" in out and "Roast Profile: Rwanda Test  v2" in out
    assert seen["verdicts"][0]["balance"] == -1 and seen["verdicts"][0]["batch"] == 3
    assert seen["reference"] is None
    prof2 = json.loads((tmp_path / "beans" / "rwanda-test" / "profile-v2.json").read_text())
    assert prof2["based_on"] == {"kind": "revision", "version": 1, "reference": None}
    assert prof2["schedule"]["drop"]["seconds"] == 160
    # no change advised: no new version
    answer = _design_answer(drop=160)
    answer["changes_from_baseline"] = []
    answer["validator_changes"] = []
    monkeypatch.setattr(analyze, "revise_profile", lambda *a, **k: (answer, "ok"))
    analyze.cmd_profile(_Args(bean="rwanda", design=False, revise=True, from_roast=None, dev=None,
                              version=None, set_reference=None, roast=None))
    assert "v2 has no cupped roasts yet" in capsys.readouterr().out
    # revise v1 explicitly (it is cupped) -> "roast it again"
    analyze.cmd_profile(_Args(bean="rwanda", design=False, revise=True, from_roast=None, dev=None,
                              version=1, set_reference=None, roast=None))
    assert "no change: roast v1 again" in capsys.readouterr().out
    assert not (tmp_path / "beans" / "rwanda-test" / "profile-v3.json").exists()


def test_export_uses_bt_only_for_a_borrowed_envelope(tmp_path, monkeypatch, capsys):
    """A new bean borrows the Rwanda's envelope; its clock fallbacks would be
    another bean's timing, so the export is temperature-triggered only."""
    logs = _setup(tmp_path, monkeypatch)
    _write_staircase_alog(logs, "r3", 3, "Rwanda Test", "2026-09-13", "11:13")   # borrowing needs 3 roasts
    analyze.cmd_scan(_Args(force=False))
    monkeypatch.setattr(analyze, "design_profile", lambda *a, **k: (_design_answer(), "ok"))
    analyze.cmd_profile(_Args(bean="Ethiopia New", design=True, revise=False, from_roast=None, dev=None,
                              version=None, set_reference=None, roast=None))
    out = capsys.readouterr().out
    assert "borrowed from Rwanda Test" in out
    analyze.cmd_export_alarms(_Args(bean="Ethiopia New", version=None, mode=None, manual_drop=True,
                                    ear_latency="auto", no_warmup=False, cool=None, install_local=False))
    out = capsys.readouterr().out
    assert "without clock fallbacks" in out
    assert "Heater 90%" in out and " or +" not in out.split("Heater 90%")[0].splitlines()[-1]
    alrm = json.loads((tmp_path / "beans" / "ethiopia-new" / "alarms" / "profile-v1-bt.alrm").read_text())
    heater_rows = [i for i, a in enumerate(alrm["alarmactions"]) if a == 6 and alrm["alarmtimes"][i] == 0]
    assert heater_rows and all(alrm["alarmoffsets"][i] == 0 for i in heater_rows)
    # an explicit mode still wins
    analyze.cmd_export_alarms(_Args(bean="Ethiopia New", version=None, mode="paired", manual_drop=True,
                                    ear_latency="auto", no_warmup=False, cool=None, install_local=False))
    assert " or +" in capsys.readouterr().out
