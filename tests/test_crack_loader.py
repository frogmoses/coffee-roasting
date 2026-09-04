"""Tests for the ear sidecar loader and its analyzer integration (no audio)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import analyze
import roast_analysis
from crack_loader import extract_audio_data, match_crack_to_roast, anchor_charge_epoch
from llm_recommender import _curated_metrics
from roast_analysis import select_prior_roasts
from roast_metrics import add_audio_metrics

CHARGE_EPOCH = 1_787_605_372.0


def _roast_data(fc_idx=300, roast_epoch=0):
    """Synthetic extracted roast: 2s sampling, CHARGE at idx 0, FCs at fc_idx."""
    timex = [float(i * 2) for i in range(400)]
    bt = [250.0 - i for i in range(20)] + [230.0 + i * 0.4 for i in range(380)]
    return {"timex": timex, "bt": bt, "timeindex": [0, 150, fc_idx, 0, 0, 0, 399, 0],
            "roast_epoch": roast_epoch}


def _write_crack(directory, session_id, uuid="", cracks=(), fc=None, charge_epoch=CHARGE_EPOCH,
                 armed=None):
    payload = {
        "schema_version": 1, "session_id": session_id, "bean_name": "Rwanda",
        "roast_uuid": uuid, "batch_nr": 0, "mode": "record-only",
        "artisan_events": {"charge": 0.0}, "charge_epoch": charge_epoch,
        "armed": armed or {"elapsed": 300.0, "source": "DRY"},
        "capture": {"peak_dbfs": -9.5, "clipped_blocks": 0, "overflows": 0},
        "cracks": list(cracks), "fc_detected": fc, "notes": "",
    }
    path = directory / f"crack_{session_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _crack(elapsed, armed=True):
    return {"elapsed": elapsed, "epoch": CHARGE_EPOCH + elapsed, "peak_db": 14.0,
            "dur_ms": 3.0, "flatness": 0.4, "armed": armed}


def _fc(elapsed):
    return {"elapsed": elapsed, "epoch": CHARGE_EPOCH + elapsed, "first_crack_elapsed": elapsed,
            "count_in_window": 4, "window_s": 20.0, "n": 4, "declared_at_elapsed": elapsed + 15,
            "peak_cpm": 30.0, "peak_cpm_elapsed": elapsed + 30}


def test_uuid_match_beats_date_match(tmp_path):
    _write_crack(tmp_path, "2026-09-06_1000", uuid="aaa")
    _write_crack(tmp_path, "2026-09-07_1000", uuid="bbb")
    got = match_crack_to_roast("2026-09-06", "10:00", "bbb", captures_dir=tmp_path)
    assert got["session_id"] == "2026-09-07_1000"


def test_time_tiebreak_handles_seconds_in_roast_time(tmp_path):
    _write_crack(tmp_path, "2026-09-06_1000")
    _write_crack(tmp_path, "2026-09-06_1130")
    got = match_crack_to_roast("2026-09-06", "11:25:41", "", captures_dir=tmp_path)
    assert got["session_id"] == "2026-09-06_1130"


def test_extract_audio_offsets_against_mark(tmp_path):
    # Mark at idx 300 = 600s; audio says 588s -> 12s before the mark
    path = _write_crack(tmp_path, "2026-09-06_1000",
                        cracks=[_crack(200, armed=False)] + [_crack(t) for t in (588, 594, 599, 603)],
                        fc=_fc(588))
    data = json.loads(path.read_text())
    audio = extract_audio_data(data, _roast_data())
    assert audio["detected_time"] == 588
    assert audio["mark_time"] == 600
    assert audio["offset"] == -12 and not audio["mark_suspect"]
    assert audio["crack_count"] == 5 and audio["cracks_after_arm"] == 4
    assert audio["detected_bt"] is not None
    assert "12s before your mark" in audio["details"]


def test_extract_audio_flags_far_mark_and_no_fc(tmp_path):
    path = _write_crack(tmp_path, "2026-09-06_1000", cracks=[_crack(540)], fc=_fc(540))
    audio = extract_audio_data(json.loads(path.read_text()), _roast_data())
    assert audio["offset"] == -60 and audio["mark_suspect"]

    path = _write_crack(tmp_path, "2026-09-06_1100", cracks=[_crack(610)], fc=None)
    audio = extract_audio_data(json.loads(path.read_text()), _roast_data())
    assert audio["detected_time"] is None
    assert "heard no first crack" in audio["details"]

    assert extract_audio_data({"cracks": [], "fc_detected": None}, _roast_data()) is None
    assert extract_audio_data(None, _roast_data()) is None


def test_reanchor_from_roast_epoch_when_charge_never_arrived(tmp_path):
    # WebSocket was down: sidecar has no charge_epoch, cracks have epochs only
    cracks = [{"epoch": CHARGE_EPOCH + t, "elapsed": None, "peak_db": 12.0, "dur_ms": 3.0,
               "flatness": 0.4, "armed": False} for t in (590, 596, 601, 605)]
    fc = {"elapsed": None, "epoch": CHARGE_EPOCH + 590, "first_crack_elapsed": None,
          "count_in_window": 4, "window_s": 20.0, "n": 4, "declared_at_elapsed": None,
          "peak_cpm": 25.0, "peak_cpm_elapsed": None}
    path = _write_crack(tmp_path, "2026-09-06_1000", cracks=cracks, fc=fc, charge_epoch=None,
                        armed={"elapsed": 300.0, "source": "manual"})
    data = json.loads(path.read_text())
    # roastepoch is ON time; CHARGE is at timex[timeindex[0]] seconds after it
    roast = _roast_data(roast_epoch=CHARGE_EPOCH - 150)
    roast["timex"] = [150.0 + i * 2 for i in range(400)]
    assert anchor_charge_epoch(data, roast) == CHARGE_EPOCH
    audio = extract_audio_data(data, roast)
    assert audio["detected_time"] == 590 and audio["offset"] == -10
    assert audio["cracks_after_arm"] == 4


def test_add_audio_metrics_and_prior_roasts_carry_offset():
    metrics = add_audio_metrics({"fc_time": 600}, {"offset": -12, "detected_time": 588})
    assert metrics["fc_audio"]["offset"] == -12
    history = {
        "1_Rwanda_2026-09-01": {"roast_id": "1_Rwanda_2026-09-01", "title": "Rwanda",
                                "roast_date": "2026-09-01", "batch_nr": 1,
                                "metrics": {"fc_audio": {"offset": -12}}},
    }
    prior = select_prior_roasts(history, "2_Rwanda_2026-09-06", "Rwanda", "2026-09-06", 2)
    assert prior[0]["fc_audio_offset"] == -12
    curated = _curated_metrics({"fc_audio": {"detected_time": 588, "offset": -12,
                                             "mark_suspect": False, "details": "x"}})
    assert curated["fc_audio_check"]["offset"] == -12


def test_scan_picks_up_sidecar(tmp_path, monkeypatch):
    from test_analysis_and_cli import _patched_env, _write_alog, _Args
    logs = _patched_env(tmp_path, monkeypatch)
    _write_alog(logs, "b1", 1, "Rwanda", "2026-09-06", "10:12")
    caps = tmp_path / "caps"
    caps.mkdir()
    _write_crack(caps, "2026-09-06_1012", uuid="uuid-b1",
                 cracks=[_crack(t) for t in (588, 594, 599, 603)], fc=_fc(588))
    monkeypatch.setenv("CRACK_CAPTURES_DIR", str(caps))
    analyze.cmd_scan(_Args(force=False))
    history = json.loads((tmp_path / "history.json").read_text())
    fc_audio = history["1_Rwanda_2026-09-06"]["metrics"]["fc_audio"]
    assert fc_audio["detected_time"] == 588 and fc_audio["offset"] == -12
