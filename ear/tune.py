"""Offline tuning: run the detector over a saved roast WAV and grade it.

    python tune.py captures/crack_2026-09-06_1012.wav --sidecar captures/crack_2026-09-06_1012.json --alog "~/coffee-roasts/#21_26-09-06_1012.alog"

Prints a 10-second crack histogram from CHARGE with DRY/FCs/DROP marks, the
audio FC verdict against the operator's mark, and (with --sweep) a table of
the same over a range of one parameter. The detector code path is the same
one the live session uses, so what tunes here applies unchanged.

Roast-time anchoring: CHARGE's position inside the WAV comes from the
sidecar (charge_epoch - capture.start_epoch), or --charge-at SEC. The .alog
supplies the marks (timeindex/timex) and roastepoch; the difference between
roastepoch and charge_epoch is printed so we learn which moment Artisan
stamps.
"""

import argparse
import ast
import json
import os
import sys
import wave
from pathlib import Path

import numpy as np

from crack_detector import DETECTOR_DEFAULTS, FC_RULE_DEFAULTS, FirstCrackTracker, detect_cracks


def load_wav(path, raw_pcm=False, sample_rate=48000):
    """WAV -> (int16 mono numpy, sample_rate). --raw-pcm reads a headerless
    int16 file (a recording whose header never got written)."""
    p = Path(path).expanduser()
    if raw_pcm:
        data = np.fromfile(p, dtype=np.int16)
        return data, sample_rate
    with wave.open(str(p), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        ch = w.getnchannels()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype=np.int16)
    if ch > 1:
        data = data.reshape(-1, ch)[:, 0]
    return data, sr


def load_alog_marks(path):
    """Marks (seconds from CHARGE) and roastepoch from an Artisan .alog."""
    raw = ast.literal_eval(Path(path).expanduser().read_text(encoding="utf-8"))
    timex = raw.get("timex", [])
    ti = raw.get("timeindex", [])
    charge_idx = max(ti[0], 0) if ti else 0
    charge_t = timex[charge_idx] if timex else 0.0
    marks = {}
    for i, name in enumerate(["CHARGE", "DRY", "FCs", "FCe", "SCs", "SCe", "DROP"]):
        if i < len(ti) and (ti[i] > 0 or i == 0) and ti[i] < len(timex):
            marks[name] = timex[ti[i]] - charge_t
    bt = raw.get("temp2", [])
    return {
        "marks": marks,
        "roastepoch": raw.get("roastepoch"),
        "timex": [t - charge_t for t in timex],
        "bt": bt,
        "title": raw.get("title", ""),
        "batch_nr": raw.get("roastbatchnr", 0),
    }


def bt_at(alog, elapsed):
    """BT nearest to `elapsed` seconds from CHARGE (None if no data)."""
    if not alog or not alog["timex"]:
        return None
    tx = alog["timex"]
    i = min(range(len(tx)), key=lambda k: abs(tx[k] - elapsed))
    return alog["bt"][i] if abs(tx[i] - elapsed) <= 5 and i < len(alog["bt"]) else None


def fmt(seconds):
    if seconds is None:
        return "--:--"
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def run(samples, sr, charge_at, params, rule, arm_at):
    """Detect cracks, assign elapsed, run the FC rule. Returns (cracks, fc)."""
    cracks = detect_cracks(samples, sr, params)
    tracker = FirstCrackTracker(**rule)
    tracker.arm(arm_at, "manual")
    fc = None
    for c in cracks:
        c["elapsed"] = round(c["stream_time"] - charge_at, 1)
        c["armed"] = c["elapsed"] >= arm_at
        if c["armed"]:
            got = tracker.add(c["elapsed"], c["epoch"])
            if got is not None:
                fc = got
    return cracks, tracker.finalize() if fc else None, tracker


def print_timeline(cracks, marks, end_s, bin_s=10):
    """10-second histogram of armed cracks from CHARGE with roast marks."""
    n_bins = int(end_s // bin_s) + 1
    counts = [0] * n_bins
    for c in cracks:
        if c["elapsed"] is not None and 0 <= c["elapsed"] < n_bins * bin_s:
            counts[int(c["elapsed"] // bin_s)] += 1
    mark_by_bin = {}
    for name, t in marks.items():
        if t is not None and t >= 0:
            mark_by_bin.setdefault(int(t // bin_s), []).append(f"{name} {fmt(t)}")
    print(f"\n  time   cracks/{bin_s}s")
    for b in range(n_bins):
        bar = "#" * min(counts[b], 40)
        tag = "   <- " + ", ".join(mark_by_bin[b]) if b in mark_by_bin else ""
        if counts[b] or tag:
            print(f"  {fmt(b * bin_s):>5}  {counts[b]:3d} {bar}{tag}")


def verdict(fc, marks, alog):
    """One line: audio FC vs the operator's mark."""
    if fc is None:
        return "no first crack declared by the audio rule"
    bt = bt_at(alog, fc["elapsed"])
    where = f" @ {bt:.0f}F" if bt is not None else ""
    line = f"FC by audio {fmt(fc['elapsed'])}{where} (first crack {fmt(fc['first_crack_elapsed'])}, peak {fc['peak_cpm']:.0f}/min)"
    mark = marks.get("FCs")
    if mark is not None:
        off = fc["elapsed"] - mark
        rel = "at the mark" if abs(off) <= 5 else (f"{abs(off):.0f}s before mark" if off < 0 else f"{off:.0f}s after mark")
        line += f"; operator mark {fmt(mark)} -> {rel}"
    return line


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tune the ear detector on a saved roast WAV")
    ap.add_argument("wav")
    ap.add_argument("--sidecar", help="crack_*.json from the same session (anchors CHARGE, supplies events)")
    ap.add_argument("--alog", help="Artisan .alog for the roast (marks, BT, roastepoch)")
    ap.add_argument("--charge-at", type=float, help="seconds into the WAV where CHARGE happened")
    ap.add_argument("--arm-at", type=float, help="seconds after CHARGE to arm the FC rule (default: DRY from sidecar/.alog, else 300)")
    ap.add_argument("--thresh", type=float, help="thresh_db")
    ap.add_argument("--band", type=float, nargs=2, metavar=("LO", "HI"))
    ap.add_argument("--max-dur", type=float, help="max_dur_ms")
    ap.add_argument("--min-flatness", type=float)
    ap.add_argument("--n", type=int, help="cracks needed in the window")
    ap.add_argument("--window", type=float, help="window seconds")
    ap.add_argument("--min-elapsed", type=float, help="earliest seconds after CHARGE the rule may fire (default 240; use 0 for bench clips)")
    ap.add_argument("--sweep", help="param=v1,v2,... e.g. thresh=8,10,12,14")
    ap.add_argument("--raw-pcm", action="store_true", help="read a headerless int16 file")
    ap.add_argument("--verbose", "-v", action="store_true", help="list every crack")
    ap.add_argument("--plot", help="write a PNG (needs matplotlib)")
    args = ap.parse_args(argv)

    samples, sr = load_wav(args.wav, raw_pcm=args.raw_pcm)
    sidecar = json.loads(Path(args.sidecar).expanduser().read_text()) if args.sidecar else {}
    alog = load_alog_marks(args.alog) if args.alog else None

    # Where is CHARGE inside the WAV?
    charge_at = args.charge_at
    if charge_at is None and sidecar.get("charge_epoch") and (sidecar.get("capture") or {}).get("start_epoch"):
        charge_at = sidecar["charge_epoch"] - sidecar["capture"]["start_epoch"]
    if charge_at is None:
        print("No CHARGE anchor (use --sidecar or --charge-at); treating WAV start as CHARGE")
        charge_at = 0.0

    marks = dict(alog["marks"]) if alog else {}
    # Sidecar events use lowercase keys; map them to Artisan's mark names
    names = {"charge": "CHARGE", "dry": "DRY", "fcs": "FCs", "fce": "FCe",
             "scs": "SCs", "sce": "SCe", "drop": "DROP"}
    for k, v in (sidecar.get("artisan_events") or {}).items():
        if k in names:
            marks.setdefault(names[k], v)
    if alog and alog.get("roastepoch") and sidecar.get("charge_epoch"):
        diff = sidecar["charge_epoch"] - alog["roastepoch"]
        print(f"roastepoch vs CHARGE epoch: CHARGE is {diff:+.1f}s after roastepoch")

    arm_at = args.arm_at
    if arm_at is None:
        arm_at = marks.get("DRY", 300.0)

    params = dict(DETECTOR_DEFAULTS)
    if args.thresh is not None:
        params["thresh_db"] = args.thresh
    if args.band:
        params["band"] = list(args.band)
    if args.max_dur is not None:
        params["max_dur_ms"] = args.max_dur
    if args.min_flatness is not None:
        params["min_flatness"] = args.min_flatness
    rule = dict(FC_RULE_DEFAULTS)
    if args.n is not None:
        rule["n"] = args.n
    if args.window is not None:
        rule["window_s"] = args.window
    if args.min_elapsed is not None:
        rule["min_elapsed_s"] = args.min_elapsed

    duration = len(samples) / sr
    print(f"{Path(args.wav).name}: {duration:.0f}s @ {sr} Hz; CHARGE at {charge_at:.1f}s into the file; arm at T+{fmt(arm_at)}")

    if args.sweep:
        name, values = args.sweep.split("=")
        key = {"thresh": "thresh_db", "max-dur": "max_dur_ms", "min-flatness": "min_flatness",
               "n": "n", "window": "window_s"}.get(name, name)
        print(f"\n  {name:>12}  cracks(armed)  FC by audio  vs mark  peak/min  pre-arm cracks")
        for v in values.split(","):
            v = float(v)
            p2, r2 = dict(params), dict(rule)
            if key in r2:
                r2[key] = int(v) if key == "n" else v
            else:
                p2[key] = v
            cracks, fc, _ = run(samples, sr, charge_at, p2, r2, arm_at)
            armed = [c for c in cracks if c["armed"]]
            off = ""
            if fc and marks.get("FCs") is not None:
                off = f"{fc['elapsed'] - marks['FCs']:+.0f}s"
            print(f"  {v:>12g}  {len(armed):13d}  {fmt(fc['elapsed']) if fc else '--':>11}  {off:>7}  "
                  f"{fc['peak_cpm'] if fc else 0:8.0f}  {len(cracks) - len(armed):14d}")
        return

    cracks, fc, tracker = run(samples, sr, charge_at, params, rule, arm_at)
    end_s = max(duration - charge_at, marks.get("DROP", 0) or 0)
    print_timeline([c for c in cracks if c["armed"]], marks, end_s)
    print(f"\n  cracks: {sum(1 for c in cracks if c['armed'])} armed, {sum(1 for c in cracks if not c['armed'])} before arming")
    print("  " + verdict(fc, marks, alog))
    if args.verbose:
        print("\n  elapsed   peak_db  dur_ms  flat  armed")
        for c in cracks:
            print(f"  {fmt(c['elapsed']):>7}  {c['peak_db']:7.1f}  {c['dur_ms']:6.1f}  {c['flatness']:.2f}  {'y' if c['armed'] else '-'}")
    if args.plot:
        _plot(args.plot, samples, sr, charge_at, cracks, marks, fc, params)


def _plot(out, samples, sr, charge_at, cracks, marks, fc, params):
    """Band level trace with crack ticks and roast marks (matplotlib optional)."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed; skipping plot")
        return
    from crack_detector import ClickDetector
    det = ClickDetector(params)
    levels, floors, times = [], [], []
    block = 4800
    for i in range(0, len(samples), block):
        det.feed(samples[i:i + block], i / sr)
        times.append(i / sr - charge_at)
        levels.append(det.level())
        floors.append(det.floor_db())
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(times, levels, lw=0.5, label="band level dB")
    ax.plot(times, floors, lw=0.8, label="floor dB")
    for c in cracks:
        ax.axvline(c["elapsed"], color="g" if c["armed"] else "0.7", alpha=0.4, lw=0.6)
    for name, t in marks.items():
        ax.axvline(t, color="k", ls="--", lw=0.8)
        ax.text(t, ax.get_ylim()[1], name, rotation=90, va="top", fontsize=8)
    if fc:
        ax.axvline(fc["elapsed"], color="r", lw=1.5, label="FC by audio")
    ax.set_xlabel("seconds from CHARGE")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(out, dpi=120)
    print(f"  plot written: {out}")


if __name__ == "__main__":
    main()
