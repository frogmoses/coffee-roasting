"""artisan_conf: Qt INI round trip, decoding of the control tables, edits."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import artisan_conf
from artisan_conf import ArtisanConf, format_value, parse_value

# Lines lifted from the roaster's real file (2026-09-13)
SAMPLE = r"""[General]
Geometry=@ByteArray(\x1\xd9\xd0\xcb\0\x3\0\0\0\0)
Mode=F
Phases=0, 300, 360, 450
autoDry=true

[DefaultButtons]
buttonactions=21, 21, 21, 21, 0, 0, 8, 8
buttonactionstrings=send({\"event\":\"CHARGE\"}), send({\"event\":\"DRY\"}), send({\"event\":\"FCs\"}), send({\"event\":\"FCe\"}), , , "heater(0);fan(10);motor(1);solenoid(1);stirrer(1)", "heater(0);fan(0);motor(0);solenoid(0);stirrer(0)"
buttonvisibility=true, false, true, false, false, false, true, true
extrabuttonactions=21, 21, 0
extrabuttonactionstrings=send({\"event\":\"ON\"}), send({\"event\":\"OFF\"}), 

[ExtraEventButtons]
extraeventbuttoncolor=#997c5f, #b79f87, yellow
extraeventbuttontextcolor=#ffffff, #ffffff, black
extraeventsactions=10, 9, 8
extraeventsactionstrings=motor(1), , 
extraeventsdescriptions=Motor On, , 
extraeventslabels=MOTOR\n\\1, \\t\n+10, \\t\n-10
extraeventstypes=4, 5, 8
extraeventsvalues=1, 2, -2
extraeventsvisibility=1, 1, 1

[Sliders]
slideractions=6, 0, 0, 5
slidervisibilities=1, 0, 0, 1
"""


def _conf(text=SAMPLE):
    return ArtisanConf(text.rstrip("\n").split("\n"))


def test_parse_and_format_follow_qt_rules():
    assert parse_value("0, 300, 360, 450") == ["0", "300", "360", "450"]
    assert parse_value("a, , ") == ["a", "", ""]
    assert parse_value(r'send({\"event\":\"CHARGE\"})') == ['send({"event":"CHARGE"})']
    assert parse_value(r'"heater(0);fan(10)", x') == ["heater(0);fan(10)", "x"]
    assert parse_value(r"MOTOR\n\\1") == ["MOTOR\n\\1"]
    assert parse_value(r"\x41, B") == ["A", "B"]   # hex escape runs to the next non-hex char, as in Qt
    # elements with , ; = or edge spaces get quoted; quotes and newlines escaped
    assert format_value(["heater(0);fan(10)", "x"]) == '"heater(0);fan(10)", x'
    assert format_value(['send({"event":"ON"})']) == r'send({\"event\":\"ON\"})'
    assert format_value(["MOTOR\n\\1"]) == r"MOTOR\n\\1"
    assert format_value(["a,b", " lead"]) == '"a,b", " lead"'
    assert format_value(42) == "42"
    with pytest.raises(ValueError):
        parse_value("@ByteArray(abc)")


def test_every_sample_line_round_trips():
    conf = _conf()
    assert conf.roundtrip_mismatches() == []


@pytest.mark.skipif(not artisan_conf.DEFAULT_CONF.exists(), reason="no pulled Artisan.conf")
def test_real_roaster_file_round_trips():
    conf = ArtisanConf.load(artisan_conf.DEFAULT_CONF)
    assert conf.roundtrip_mismatches() == []
    assert conf.get("SerialPort", "comport") is not None


def test_decoded_views_match_the_roaster_setup():
    conf = _conf()
    defaults = {r["button"]: r for r in conf.default_buttons()}
    assert defaults["CHARGE"]["action"] == "WebSocket Command"
    assert defaults["CHARGE"]["command"] == 'send({"event":"CHARGE"})'
    assert defaults["DROP"]["action"] == "Hottop Command"
    assert defaults["DROP"]["command"].startswith("heater(0);fan(10)")
    assert defaults["OFF"]["command"] == 'send({"event":"OFF"})'
    assert defaults["DRY"]["visible"] is False and defaults["FCs"]["visible"] is True

    custom = conf.custom_buttons()
    assert [b["action"] for b in custom] == ["Hottop Command", "Hottop Fan", "Hottop Heater"]
    assert custom[0]["label"] == "MOTOR \\1" and custom[0]["command"] == "motor(1)"
    assert custom[1]["event_type"] == "±Fan" and custom[2]["event_type"] == "±Heater"

    sliders = conf.sliders()
    assert sliders[0]["event_type"] == "Fan" and sliders[0]["action"] == "Hottop Fan"
    assert sliders[3]["event_type"] == "Heater" and sliders[3]["action"] == "Hottop Heater"
    assert [s["visible"] for s in sliders] == [True, False, False, True]
    assert conf.alarms() == []


def test_set_keeps_other_lines_and_adds_keys_in_place():
    conf = _conf()
    before = list(conf.lines)
    conf.set_list("General", "Phases", ["0", "300", "365", "450"])
    conf.set("General", "KeepON", "true")              # new key: lands inside [General]
    conf.set_list("Alarms", "alarmflag", ["1", "1"])   # new section: appended at the end
    assert conf.get("General", "Phases") == "0, 300, 365, 450"
    assert conf.lines.index("KeepON=true") < conf.lines.index("[DefaultButtons]")
    assert conf.sections()[-1] == "Alarms" and conf.get("Alarms", "alarmflag") == "1, 1"
    # every original line other than Phases is still present and in order
    kept = [l for l in conf.lines if l in before and l]
    assert kept == [l for l in before if l and not l.startswith("Phases=")]
    assert conf.get("General", "Geometry", "").startswith("@ByteArray(")
    conf.delete("General", "KeepON")
    assert conf.get("General", "KeepON") is None


def test_save_and_load_are_byte_exact(tmp_path):
    path = tmp_path / "Artisan.conf"
    path.write_text(SAMPLE, encoding="utf-8")
    conf = ArtisanConf.load(path)
    conf.save(path)
    assert path.read_text(encoding="utf-8") == SAMPLE


def test_cli_get_set_check(tmp_path, capsys):
    path = tmp_path / "Artisan.conf"
    path.write_text(SAMPLE, encoding="utf-8")
    assert artisan_conf.main(["-f", str(path), "check"]) == 0
    assert artisan_conf.main(["-f", str(path), "get", "Sliders", "slideractions"]) == 0
    assert capsys.readouterr().out.strip().endswith("6, 0, 0, 5")
    assert artisan_conf.main(["-f", str(path), "set", "General", "KeepON", "true"]) == 0
    assert artisan_conf.main(["-f", str(path), "set", "General", "Phases", "0", "300", "370", "450"]) == 0
    reloaded = ArtisanConf.load(path)
    assert reloaded.get("General", "KeepON") == "true"
    assert reloaded.get_list("General", "Phases") == ["0", "300", "370", "450"]
    assert artisan_conf.main(["-f", str(path), "get", "General", "nothing"]) == 1
    assert artisan_conf.main(["-f", str(path), "show", "buttons"]) == 0
    assert "Hottop Command" in capsys.readouterr().out
