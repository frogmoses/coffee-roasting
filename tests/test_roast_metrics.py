"""Tests for metric extraction, target comparison, and RoR analysis."""

import sys
from pathlib import Path

# Make project modules importable when running pytest from the repo root
sys.path.insert(0, str(Path(__file__).parent.parent))

from roast_metrics import (
    TARGETS,
    assess_ror_smoothness,
    compare_to_targets,
    extract_metrics,
    validate_metrics,
)


def _build_roast(ror_profile, interval=2.0, charge_bt=250.0):
    """Build synthetic roast data from a piecewise RoR profile.

    ror_profile: list of (duration_seconds, ror_f_per_min) segments laid
    end to end after a fixed drying ramp. Returns extracted-roast-data
    style dict with timex/bt/timeindex covering CHARGE->DROP.
    """
    timex = []
    bt = []
    t = 0.0
    temp = charge_bt

    # Drying ramp: drop to TP then climb to 300F (DRY) at ~25 F/min
    # TP recovery: 30s falling to 160F
    for _ in range(int(30 / interval)):
        timex.append(t)
        bt.append(temp)
        temp -= (charge_bt - 160.0) / (30 / interval)
        t += interval
    # Climb to 300F at 25 F/min
    while temp < 300.0:
        timex.append(t)
        bt.append(temp)
        temp += 25.0 * interval / 60.0
        t += interval
    dry_idx = len(timex) - 1

    # Apply the requested RoR segments (maillard + development)
    boundaries = []
    for duration, ror in ror_profile:
        steps = int(duration / interval)
        for _ in range(steps):
            timex.append(t)
            bt.append(temp)
            temp += ror * interval / 60.0
            t += interval
        boundaries.append(len(timex) - 1)

    # FC at the end of the first profile segment, DROP at the very end
    fc_idx = boundaries[0] if len(boundaries) > 1 else 0
    drop_idx = len(timex) - 1
    timeindex = [0, dry_idx, fc_idx, 0, 0, 0, drop_idx, 0]

    return {
        "timex": timex,
        "bt": bt,
        "et": [v + 40 for v in bt],
        "timeindex": timeindex,
        "events": [],
    }


def test_compare_skips_unrecorded_metrics():
    """Metrics of 0/-1 (event not recorded) must not produce LOW noise."""
    metrics = {key: 0 for key in TARGETS}
    metrics["fc_bt"] = -1.0
    metrics["heat_adjustments"] = 0  # a real value, should be compared
    comparisons = compare_to_targets(metrics)
    compared_keys = {c["metric"] for c in comparisons}
    assert compared_keys == {"heat_adjustments"}
    assert comparisons[0]["status"] == "OK"


def test_compare_dev_time_range_formats_as_mmss():
    """Seconds-based range targets display as M:SS."""
    # Pick an in-range value from the active target so the test is robust
    # to targets.json recalibration
    val = int((TARGETS["dev_phase_time"]["min"] + TARGETS["dev_phase_time"]["max"]) // 2)
    metrics = {key: 0 for key in TARGETS}
    metrics["dev_phase_time"] = val
    comparisons = compare_to_targets(metrics)
    devt = [c for c in comparisons if c["metric"] == "dev_phase_time"][0]
    assert devt["status"] == "OK"
    assert devt["actual_display"] == f"{val // 60}:{val % 60:02d}"
    assert "-" in devt["target_str"]


def test_compare_dev_time_low_flags():
    """Dev time below the target range flags LOW."""
    metrics = {key: 0 for key in TARGETS}
    metrics["dev_phase_time"] = TARGETS["dev_phase_time"]["min"] - 10
    comparisons = compare_to_targets(metrics)
    devt = [c for c in comparisons if c["metric"] == "dev_phase_time"][0]
    assert devt["status"] == "!! LOW"


def test_heat_correlation_boundary():
    """4 adjustments (the target max) is low_input; 5 is high_input."""
    data = _build_roast([(240, 15.0), (100, 10.0)])
    assert assess_ror_smoothness(data, 4)["heat_correlation"] == "low_input"
    assert assess_ror_smoothness(data, 5)["heat_correlation"] == "high_input"


def test_smooth_decline_no_crash_or_flick():
    """A steady declining RoR reads smooth with no FC defects."""
    data = _build_roast([(240, 15.0), (50, 12.0), (50, 10.0)])
    result = assess_ror_smoothness(data)
    assert result["severity"] == "smooth"
    assert result["fc_crash"] is False
    assert result["fc_flick"] is False


def test_crash_and_flick_detected():
    """RoR plunging to ~1 after FC then rebounding is a crash + flick."""
    data = _build_roast([(240, 15.0), (60, 1.0), (60, 9.0)])
    result = assess_ror_smoothness(data)
    assert result["fc_crash"] is True
    assert result["fc_flick"] is True
    assert result["crash_min_ror"] is not None


def test_crash_without_flick():
    """RoR plunging after FC and staying down is a crash, not a flick."""
    data = _build_roast([(240, 15.0), (60, 1.0), (60, 1.0)])
    result = assess_ror_smoothness(data)
    assert result["fc_crash"] is True
    assert result["fc_flick"] is False


def test_gentle_wobble_after_fc_is_not_a_flick():
    """A post-FC RoR that only sags a few F/min then drifts back up is normal
    thermal noise, not a flick (regression: roast #8 wobbled ~11->8->14 with no
    crash and was wrongly flagged as a char-signature flick). Sag of ~4 F/min
    is below FLICK_MIN_SAG."""
    data = _build_roast([(240, 12.0), (60, 8.0), (60, 13.0)])
    result = assess_ror_smoothness(data)
    assert result["fc_crash"] is False
    assert result["fc_flick"] is False


def test_moderate_sag_then_rebound_is_a_flick():
    """A real flick without a full crash: RoR sags well below its FC value
    (16->9, past FLICK_MIN_SAG) then climbs back, with no near-stall (so crash
    stays False since the dip never reaches the <5 F/min crash floor)."""
    data = _build_roast([(240, 16.0), (60, 9.0), (60, 14.0)])
    result = assess_ror_smoothness(data)
    assert result["fc_crash"] is False
    assert result["fc_flick"] is True


def test_window_adapts_to_sampling_interval():
    """Same curve at 1s sampling still produces sane RoR stats."""
    data = _build_roast([(240, 15.0), (100, 10.0)], interval=1.0)
    result = assess_ror_smoothness(data)
    # Mean RoR should land near the profile values, not double/half
    assert 8.0 < result["ror_mean"] < 20.0


def test_weight_loss_zeroed_without_weight_out():
    """Artisan reports weight_loss=100 when weight-out is missing."""
    metrics = extract_metrics({
        "computed": {"weight_loss": 100.0, "weightout": 0},
        "timex": [], "bt": [], "timeindex": [], "events": [],
    })
    assert metrics["weight_loss_pct"] == 0


def test_validate_flags_negative_charge():
    """Artisan's -1 sentinel counts as a missing CHARGE temperature."""
    metrics = {"charge_bt": -1.0, "charge_et": -1.0}
    warnings = validate_metrics(metrics)
    assert any("CHARGE BT missing" in w for w in warnings)
