#!/usr/bin/env python3
"""Ear — microphone first-crack detector for the Hottop, run on the roaster.

    run_ear ear.py devices                       # list audio inputs
    run_ear ear.py level                         # per-second input level (jack/gain check)
    run_ear ear.py listen --bean "Rwanda" --record-only   # first sessions
    run_ear ear.py listen --bean "Rwanda"        # alerts on
    run_ear ear.py listen --record-now --arm-at 0   # bench test without Artisan
    run_ear ear.py show                          # summarize the latest sidecar
    run_ear ear.py tune captures/crack_X.wav --sidecar captures/crack_X.json --alog PATH

Settings (ear.conf beside this file, loaded by the run_ear wrapper):
EAR_DEVICE, EAR_PORT, EAR_CAPTURES_DIR, ARTISAN_SAVE_DIR, CRACK_RSYNC_DEST,
EAR_PUSH_WAV, EAR_ALERT_SOUND, EAR_POST_DROP_S, EAR_OFF_TIMEOUT_S. The code
reads only os.environ.
"""

import argparse
import os
import sys


def cmd_devices(args):
    from audio_capture import list_input_devices
    devices = list_input_devices()
    if not devices:
        print("No input devices found")
        return
    print(f"{'idx':>3}  {'name':<50} ch   rate   host")
    for d in devices:
        print(f"{d['index']:>3}  {d['name']:<50} {d['channels']:>2}  {d['samplerate']:>6.0f}  {d['hostapi']}")
    print("\nSet EAR_DEVICE to a unique substring (e.g. 'Sound Blaster' or 'hw:1').")
    print("If the card is busy under PipeWire: wpctl set-default <id> then EAR_DEVICE=pipewire.")


def cmd_listen(args):
    from ear_session import run_session
    run_session(
        bean_name=args.bean,
        ws_port=args.port,
        device=args.device,
        record_only=args.record_only,
        record_now=args.record_now,
        arm_at=args.arm_at,
        debug=args.debug,
    )


def cmd_level(args):
    """Print the input level once a second for a few seconds — for jack and
    gain checks without starting a session. A live capsule in a quiet room
    reads around -50 dBFS RMS with a broadband floor; a dead or mis-wired
    input reads -60 or lower with almost nothing above 3 kHz."""
    import math
    import time as _time
    import numpy as np
    from audio_capture import AudioCapture, select_device
    from crack_detector import ClickDetector
    device = select_device(args.device or os.environ.get("EAR_DEVICE", "Sound Blaster"))
    cap = AudioCapture(device)
    cap.start()
    det = ClickDetector({"sample_rate": cap.sample_rate})
    print(f"device [{device}] @ {cap.sample_rate} Hz for {args.seconds}s — snap fingers by the capsule")
    print(f"{'t':>3}  {'rms dBFS':>8}  {'peak dBFS':>9}  {'>3kHz':>6}  cracks")
    end = _time.time() + args.seconds
    sec_blocks, second, cracks = [], 0, 0
    try:
        while _time.time() < end:
            item = cap.read(timeout=0.5)
            if item is None:
                continue
            block, epoch = item
            cracks += len(det.feed(block, epoch))
            sec_blocks.append(block)
            if len(sec_blocks) * cap.blocksize >= cap.sample_rate:
                y = np.concatenate(sec_blocks).astype(np.float64) / 32768
                rms = 20 * math.log10(math.sqrt(float(np.mean(y ** 2))) + 1e-9)
                pk = 20 * math.log10(float(np.max(np.abs(y))) + 1e-9)
                spec = np.abs(np.fft.rfft(y)) ** 2
                freqs = np.fft.rfftfreq(len(y), 1 / cap.sample_rate)
                hi = spec[freqs > 3000].sum() / (spec.sum() + 1e-12) * 100
                second += 1
                print(f"{second:>3}  {rms:8.1f}  {pk:9.1f}  {hi:5.0f}%  {cracks}")
                sec_blocks = []
    finally:
        cap.stop()


def cmd_show(args):
    from ear_session import load_latest_sidecar
    from ear_display import fmt_time
    sc = load_latest_sidecar()
    if not sc:
        print("No sidecars in the captures dir")
        return
    cracks = sc.get("cracks", [])
    armed = [c for c in cracks if c.get("armed")]
    print(f"Session {sc['session_id']}  bean {sc.get('bean_name')}  mode {sc.get('mode')}")
    print(f"  roast: #{sc.get('batch_nr')} uuid {sc.get('roast_uuid') or '(not linked)'}")
    print(f"  events: {sc.get('artisan_events')}")
    cap = sc.get("capture") or {}
    print(f"  capture: {cap.get('wav_file')} {fmt_time(cap.get('duration_s'))} peak {cap.get('peak_dbfs')} dBFS "
          f"clipped {cap.get('clipped_blocks')} overflows {cap.get('overflows')}")
    print(f"  armed: {sc.get('armed')}   cracks: {len(armed)} armed / {len(cracks)} total")
    fc = sc.get("fc_detected")
    if fc:
        print(f"  FC by audio: T+{fmt_time(fc['elapsed'])} (first crack {fmt_time(fc['first_crack_elapsed'])}, "
              f"{fc['count_in_window']} in {fc['window_s']:.0f}s, peak {fc['peak_cpm']}/min)")
    else:
        print("  FC by audio: not declared")


def main():
    ap = argparse.ArgumentParser(description="Ear: microphone first-crack detector")
    sub = ap.add_subparsers(dest="command")

    sub.add_parser("devices", help="list audio input devices")

    p = sub.add_parser("listen", help="run a roast session")
    p.add_argument("--bean", default=None, help="bean name for the sidecar")
    p.add_argument("--device", default=None, help="input device substring/index (default EAR_DEVICE)")
    p.add_argument("--port", type=int, default=int(os.environ.get("EAR_PORT", "8765")))
    p.add_argument("--record-only", action="store_true", help="log everything, no alerts")
    p.add_argument("--record-now", action="store_true", help="start recording immediately (bench test)")
    p.add_argument("--arm-at", type=float, default=None, help="arm the FC rule N seconds after CHARGE (default: DRY event, else 300)")
    p.add_argument("--debug", action="store_true", help="log raw WebSocket messages")

    sub.add_parser("show", help="summarize the latest sidecar")

    lv = sub.add_parser("level", help="print input level per second (jack/gain check)")
    lv.add_argument("--device", default=None)
    lv.add_argument("--seconds", type=int, default=15)

    t = sub.add_parser("tune", help="offline tuning over a WAV (see tune.py --help)", add_help=False)
    t.add_argument("rest", nargs=argparse.REMAINDER)

    args = ap.parse_args()
    if args.command == "devices":
        cmd_devices(args)
    elif args.command == "listen":
        cmd_listen(args)
    elif args.command == "show":
        cmd_show(args)
    elif args.command == "level":
        cmd_level(args)
    elif args.command == "tune":
        import tune
        tune.main(args.rest)
    else:
        ap.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
