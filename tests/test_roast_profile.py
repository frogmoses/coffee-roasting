"""Versioned profile store: numbering, reference, roast links, export matching."""

import roast_profile as rp
import roast_schedule as rs


def _profile(slug="rwanda-rusizi-gaseke"):
    return rp.new_profile(slug, "Rwanda Rusizi Gaseke", rs.new_schedule(drop_s=150),
                          based_on={"kind": "staircase", "roast_id": "26_x"})


def test_slugify_and_title_lookup():
    assert rp.slugify("Rwanda Rusizi Gaseke") == "rwanda-rusizi-gaseke"
    assert rp.slugify("  Ethiopia  Gerba/Hechere!") == "ethiopia-gerba-hechere"
    history = {
        "a": {"title": "Rwanda Rusizi Gaseke", "roast_date": "2026-09-13"},
        "b": {"title": "Bolivia Caranavi", "roast_date": "2026-06-27"},
        "c": {"title": "Ethiopia Gerba Hechere", "roast_date": "2026-07-31"},
    }
    assert rp.find_bean_title(history, "rwanda") == "Rwanda Rusizi Gaseke"
    assert rp.find_bean_title(history, "Bolivia Caranavi") == "Bolivia Caranavi"
    assert rp.find_bean_title(history, "ethiopia-gerba") == "Ethiopia Gerba Hechere"
    assert rp.find_bean_title(history, "kenya") is None


def test_versions_increment_and_latest_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path))
    p1 = _profile()
    path = rp.save_profile(p1)
    assert path.name == "profile-v1.json" and p1["version"] == 1
    p2 = _profile()
    rp.save_profile(p2)
    assert p2["version"] == 2 and rp.list_versions("rwanda-rusizi-gaseke") == [1, 2]
    assert rp.load_profile("rwanda-rusizi-gaseke")["version"] == 2
    assert rp.load_profile("rwanda-rusizi-gaseke", 1)["version"] == 1
    assert rp.load_profile("nope") is None
    # re-saving keeps the number
    p1["status"] = "exported"
    rp.save_profile(p1)
    assert rp.list_versions("rwanda-rusizi-gaseke") == [1, 2]
    assert rp.load_profile("rwanda-rusizi-gaseke", 1)["status"] == "exported"


def test_reference_and_roast_links(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path))
    prof = _profile()
    rp.save_profile(prof)
    entry = {"roast_id": "27_Rwanda_2026-09-20", "batch_nr": 27, "roast_date": "2026-09-20",
             "metrics": {"dev_phase_time": 148, "fc_time": 640, "fc_bt": 361, "drop_bt": 382}}
    link = rp.attach_roast(prof, entry, execution={"summary": "4/4 moves executed"})
    assert link["dev_s"] == 148 and link["execution"] == "4/4 moves executed"
    assert prof["status"] == "roasted"
    rp.attach_roast(prof, entry)                       # same roast again: no duplicate
    assert len(prof["roasts"]) == 1
    assert rp.attach_verdict(prof, "27_Rwanda_2026-09-20", intake={"balance": 0}, notes="best yet")
    assert prof["roasts"][0]["cupping_intake"] == {"balance": 0} and prof["status"] == "cupped"
    assert rp.attach_verdict(prof, "missing") is None
    rp.save_profile(prof)
    ref = rp.set_reference("rwanda-rusizi-gaseke", 1, "27_Rwanda_2026-09-20")
    assert ref["version"] == 1 and rp.get_reference("rwanda-rusizi-gaseke")["roast_id"] == "27_Rwanda_2026-09-20"
    assert rp.load_profile("rwanda-rusizi-gaseke", 1)["status"] == "reference"


def test_match_profile_by_export_time(tmp_path, monkeypatch):
    monkeypatch.setenv("ROAST_BEANS_DIR", str(tmp_path))
    slug = "rwanda-rusizi-gaseke"
    p1, p2, p3 = _profile(), _profile(), _profile()
    for p in (p1, p2, p3):
        rp.save_profile(p)
    p1["exported"] = {"at": "2026-09-10T20:00:00"}
    p2["exported"] = {"at": "2026-09-20T08:30:00"}
    rp.save_profile(p1); rp.save_profile(p2); rp.save_profile(p3)    # v3 never exported
    assert rp.match_profile_to_roast(slug, "2026-09-13", "10:43")["version"] == 1
    assert rp.match_profile_to_roast(slug, "2026-09-20", "10:00")["version"] == 2
    assert rp.match_profile_to_roast(slug, "2026-09-20", "08:00")["version"] == 1
    assert rp.match_profile_to_roast(slug, "2026-09-20")["version"] == 2       # unknown time
    assert rp.match_profile_to_roast(slug, "2026-09-01") is None
    rp.mark_exported(p3, "paired", 18.0, True, [tmp_path / "x.alrm"])
    assert p3["status"] == "exported" and p3["exported"]["manual_drop"] is True
