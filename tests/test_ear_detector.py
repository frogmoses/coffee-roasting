"""Tests for the ear click detector and first-crack rule (no sound device)."""

import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, str(Path(__file__).parent.parent / "ear"))

from crack_detector import ClickDetector, FirstCrackTracker, detect_cracks  # noqa: E402

SR = 48000


def _synth(seconds, clicks_at=(), beep_at=None, burst_at=None, ramp=False,
           noise_db=-30.0, click_db=-8.0, seed=0):
    """Roaster-ish noise plus injected events, as float32 in [-1, 1].

    Noise: brown noise (cumulative white, low-frequency heavy like a drum
    motor) plus a 120 Hz hum with harmonics. Clicks: 3 ms decaying white
    bursts. Beep: 4 kHz sine, 300 ms (in-band, tonal). Burst: 150 ms white
    noise (broadband but far too long). Ramp: noise gains +6 dB over the
    last half (a fan-speed change the floor must follow).
    """
    rng = np.random.default_rng(seed)
    n = int(seconds * SR)
    t = np.arange(n) / SR
    brown = np.cumsum(rng.standard_normal(n))
    brown -= np.linspace(brown[0], brown[-1], n)
    brown /= np.max(np.abs(brown)) + 1e-9
    # A little white component so the high band isn't empty
    white = rng.standard_normal(n) * 0.05
    hum = sum(np.sin(2 * np.pi * 120 * k * t) / k for k in range(1, 6)) * 0.2
    x = brown + white + hum
    x = x / (np.max(np.abs(x)) + 1e-9) * (10 ** (noise_db / 20))
    if ramp:
        gain = np.ones(n)
        half = n // 2
        gain[half:] = np.linspace(1.0, 2.0, n - half)
        x = x * gain
    amp = 10 ** (click_db / 20)
    for c in clicks_at:
        i = int(c * SR)
        m = int(0.003 * SR)
        env = np.exp(-np.linspace(0, 6, m))
        x[i:i + m] += rng.standard_normal(m) * env * amp
    if beep_at is not None:
        i = int(beep_at * SR)
        m = int(0.3 * SR)
        x[i:i + m] += np.sin(2 * np.pi * 4000 * t[:m]) * amp
    if burst_at is not None:
        i = int(burst_at * SR)
        m = int(0.15 * SR)
        x[i:i + m] += rng.standard_normal(m) * amp
    return np.clip(x, -1, 1).astype(np.float32)


def _times(cracks):
    return [c["stream_time"] for c in cracks]


def test_detects_injected_clicks_and_nothing_else():
    clicks = [1.0, 2.5, 4.2, 5.9]
    x = _synth(7.0, clicks_at=clicks)
    cracks = detect_cracks(x, SR)
    found = _times(cracks)
    assert len(found) == len(clicks), found
    for want, got in zip(clicks, found):
        assert abs(got - want) <= 0.008
    for c in cracks:
        assert 1.0 <= c["dur_ms"] <= 30.0
        assert c["flatness"] >= 0.15


def test_rejects_tonal_beep_and_long_burst():
    x = _synth(6.0, clicks_at=[1.0], beep_at=2.0, burst_at=4.0)
    cracks = detect_cracks(x, SR)
    found = _times(cracks)
    assert len(found) == 1 and abs(found[0] - 1.0) <= 0.008, found


def test_close_clicks_merge_into_one():
    x = _synth(4.0, clicks_at=[2.0, 2.010])
    assert len(detect_cracks(x, SR)) == 1


def test_floor_tracks_slow_noise_ramp():
    x = _synth(12.0, ramp=True)
    assert detect_cracks(x, SR) == []


def test_block_size_parity():
    x = _synth(6.0, clicks_at=[1.3, 3.1, 4.8])
    a = _times(detect_cracks(x, SR, block=4800))
    b = _times(detect_cracks(x, SR, block=1024))
    assert a == b


def test_int16_input_matches_float():
    x = _synth(4.0, clicks_at=[1.5, 3.0])
    xi = (x * 32767).astype(np.int16)
    assert _times(detect_cracks(x, SR)) == _times(detect_cracks(xi, SR))


def test_epoch_anchoring():
    x = _synth(3.0, clicks_at=[2.0])
    cracks = detect_cracks(x, SR, start_epoch=1_000_000.0)
    assert abs(cracks[0]["epoch"] - 1_000_002.0) <= 0.008


def test_tracker_declares_on_nth_click_and_reports_first():
    tr = FirstCrackTracker(n=4, window_s=20.0, min_elapsed_s=0)
    assert tr.add(10.0) is None            # not armed
    tr.arm(100.0, "DRY")
    assert tr.add(50.0) is None            # before arming point
    assert tr.add(500.0) is None
    assert tr.add(506.0) is None
    assert tr.add(511.0) is None
    fc = tr.add(517.0, epoch=2_000_000.0)
    assert fc is not None
    assert fc["elapsed"] == 500.0          # onset = first crack in the window
    assert fc["count_in_window"] == 4
    assert fc["epoch"] == 2_000_000.0 - 17.0
    assert tr.add(520.0) is None           # declares only once
    assert tr.status(520.0)["crack_type"] == "first"


def test_tracker_window_excludes_stale_clicks():
    tr = FirstCrackTracker(n=3, window_s=10.0, min_elapsed_s=0)
    tr.arm(0.0, "manual")
    tr.add(0.0); tr.add(1.0)
    assert tr.add(30.0) is None            # the first two are stale
    assert tr.add(31.0) is None
    assert tr.add(32.0)["elapsed"] == 30.0


def test_tracker_respects_min_elapsed():
    tr = FirstCrackTracker(n=2, window_s=10.0, min_elapsed_s=240.0)
    tr.arm(0.0, "manual")
    assert tr.add(100.0) is None and tr.add(101.0) is None
    assert tr.accepted == []
    tr.add(300.0)
    assert tr.add(301.0) is not None


def test_detector_exposes_floor_and_level():
    det = ClickDetector()
    assert det.floor_db() is None
    det.feed(_synth(1.0))
    assert det.floor_db() is not None and det.level() is not None
