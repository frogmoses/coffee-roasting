"""Ear session: capture the roast, detect cracks, log a sidecar, alert.

Orchestration modeled on gopro/sentinel.py. Artisan connects to the
WebSocket server on ON; recording starts then so the drum/fan floor is
established before CHARGE. Events from Artisan give the roast clock
(CHARGE = T+0), arm the first-crack rule (DRY), mark DROP, and on OFF let
us link the .alog Artisan just saved (roastUUID, batch number).

Unlike the sentinel, the server stays up after DROP until OFF or a timeout,
so the UUID link actually lands in the sidecar. The sidecar and WAV are
rsynced to the dev machine (CRACK_RSYNC_DEST), where crack_loader.py
matches them to the roast log.
"""

import ast
import asyncio
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import artisan_sync
import alert
import ear_display
from audio_capture import AudioCapture, select_device
from crack_detector import ClickDetector, FirstCrackTracker, DETECTOR_DEFAULTS, FC_RULE_DEFAULTS

SCHEMA_VERSION = 1

# Arm the FC rule this long after CHARGE if Artisan never sends DRY. DRY
# lands 6-7 minutes in on this machine, so the fallback must come later or
# it pre-empts the DRY event (it did on the first live day at 300 s).
ARM_FALLBACK_S = 480.0


def _env_float(name, default):
    """Float env var with a default."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


class EarSession:
    """One roast: WebSocket events + microphone + detector + sidecar."""

    def __init__(self, bean_name=None, ws_port=8765, device=None, record_only=False,
                 record_now=False, arm_at=None, debug=False, captures_dir=None,
                 detector_params=None, fc_rule=None):
        self.bean_name = bean_name or "Unknown"
        self.ws_port = ws_port
        self.device_spec = device or os.environ.get("EAR_DEVICE", "Sound Blaster")
        self.record_only = record_only
        self.record_now = record_now
        self.arm_at = arm_at
        self.debug = debug
        self.captures_dir = Path(captures_dir or os.path.expanduser(
            os.environ.get("EAR_CAPTURES_DIR", str(Path(__file__).parent / "captures"))))
        self.post_drop_s = _env_float("EAR_POST_DROP_S", 90)
        self.off_timeout_s = _env_float("EAR_OFF_TIMEOUT_S", 600)
        self.push_wav = os.environ.get("EAR_PUSH_WAV", "1") not in ("0", "false", "no", "")

        self.session_id = time.strftime("%Y-%m-%d_%H%M")
        self.mode = "record-only" if record_only else "live"
        self.running = False
        self.charge_epoch = None
        self.charge_source = None      # "artisan", "record-now", or "missing"
        # T0 for the FC rule. Equals charge_epoch once CHARGE is seen. If the
        # ear joined mid-roast (DRY/FCs arrive with no CHARGE), a provisional
        # clock is adopted so the rule can still count cracks; elapsed values
        # are then NOT written to the sidecar — crack_loader re-anchors from
        # crack epochs and the .alog's roastepoch instead.
        self.rule_epoch = None
        self.drop_epoch = None
        self.off_received = False
        self.roast_uuid = ""
        self.batch_nr = 0
        self.notes = ""
        self.cracks = []               # every accepted click, armed or not
        self._lock = threading.Lock()
        self._last_save = 0.0
        self._wav_pushed = False
        self._finalized = False

        # The detector is built when capture starts, at the sample rate the
        # device actually opened; until then only its parameters are known
        self.detector_params = dict(detector_params or {})
        self.detector = None
        self.capture_error = None
        rule = dict(FC_RULE_DEFAULTS)
        rule.update(fc_rule or {})
        # An explicit --arm-at earlier than the default minimum (bench runs
        # with --arm-at 0) should be allowed to declare; a real roast keeps
        # the 240 s guard.
        if arm_at is not None:
            rule["min_elapsed_s"] = min(rule["min_elapsed_s"], float(arm_at))
        self.tracker = FirstCrackTracker(**rule)
        self.capture = None
        self.wav_path = None
        self._audio_thread = None
        self.device_index = None
        self.device_name = ""

        self.artisan = artisan_sync.ArtisanServer(port=ws_port, debug=debug)
        self.artisan.on_event(self._on_artisan_event)
        self.artisan.on_connect(self._on_artisan_connect)
        self.artisan.on_disconnect(self._on_artisan_disconnect)

    # ---- Artisan callbacks (run on the asyncio thread) ----

    def _on_artisan_event(self, event_name, elapsed):
        print(f"  Artisan: {event_name} at T+{ear_display.fmt_time(elapsed)}")
        if event_name in ("DRY", "FCs") and self._t0() is None:
            self._adopt_late_clock(event_name)
        if event_name == "CHARGE":
            self.charge_epoch = self.artisan.charge_time
            self.rule_epoch = self.charge_epoch
            self.charge_source = "artisan"
        elif event_name == "DRY":
            if self.charge_epoch is not None:
                self.tracker.arm(elapsed, "DRY")
            else:
                self.tracker.arm(self._rule_elapsed(time.time()), "DRY (late start)")
        elif event_name == "FCs" and self.tracker.armed_at is None:
            # Joined even later: arm now so the burst still gets an onset
            self.tracker.arm(self._rule_elapsed(time.time()), "FCs (late start)")
        elif event_name == "DROP":
            self.drop_epoch = time.time()
            self._save_sidecar()
            self._push([self._sidecar_path()])
        elif event_name == "OFF":
            self.off_received = True

    def _t0(self):
        """Epoch the FC rule counts from: CHARGE, else the provisional clock."""
        return self.charge_epoch if self.charge_epoch is not None else self.rule_epoch

    def _rule_elapsed(self, epoch):
        """Seconds since _t0() for an epoch, or None before any clock exists."""
        t0 = self._t0()
        return None if t0 is None else epoch - t0

    def _adopt_late_clock(self, event_name):
        """The ear joined after CHARGE (batch #25 on 2026-09-13: two false
        starts, then a listen that connected after the charge). With no roast
        clock every crack stayed un-armed and FC was never declared. Assume
        the roast is ARM_FALLBACK_S in — DRY lands 6-7 min after charge, so
        the rule's minimum-elapsed gate is satisfied — and count from here.
        """
        self.rule_epoch = time.time() - ARM_FALLBACK_S
        self.charge_source = "missing"
        print(f"  !! {event_name} arrived with no CHARGE seen (ear started after charge?). "
              f"Counting cracks from now; the analyzer rebuilds roast times from the .alog.")

    def _on_artisan_connect(self):
        print("  Artisan connected")
        if self.capture is None and self.capture_error is None:
            # A capture failure must not take the WebSocket handler down:
            # events are still worth logging, and the display shows the error
            try:
                self.start_capture()
            except Exception as e:
                self.capture_error = str(e)
                print(f"  !! capture failed: {e}")

    def _on_artisan_disconnect(self):
        print("  Artisan disconnected")

    # ---- capture and detection ----

    def start_capture(self):
        """Resolve the device, open the WAV, start the stream and the audio thread."""
        self.device_index = select_device(self.device_spec)
        from audio_capture import list_input_devices
        self.device_name = next((d["name"] for d in list_input_devices()
                                 if d["index"] == self.device_index), str(self.device_index))
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.wav_path = self.captures_dir / f"crack_{self.session_id}.wav"
        capture = AudioCapture(
            self.device_index,
            sample_rate=int(self.detector_params.get("sample_rate", DETECTOR_DEFAULTS["sample_rate"])),
            wav_path=self.wav_path,
        )
        try:
            capture.start()
        except Exception:
            # Leave no empty WAV behind a failed start
            if self.wav_path and self.wav_path.exists() and self.wav_path.stat().st_size == 0:
                self.wav_path.unlink()
            raise
        params = dict(self.detector_params)
        params["sample_rate"] = capture.sample_rate
        self.detector = ClickDetector(params)
        self.capture = capture
        print(f"  Recording {self.wav_path.name} from [{self.device_index}] {self.device_name} "
              f"@ {capture.sample_rate} Hz")
        # Bench mode without Artisan: treat recording start as CHARGE so the
        # FC rule and elapsed times work; a real CHARGE overrides this.
        if self.record_now and self.charge_epoch is None:
            self.charge_epoch = self.capture.start_epoch
            self.charge_source = "record-now"
        self._audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self._audio_thread.start()

    def _audio_loop(self):
        """Consumer thread: WAV + detector + FC rule + alert."""
        while self.capture is not None and self.capture.running:
            item = self.capture.read(timeout=0.5)
            if item is None:
                continue
            block, epoch = item
            for crack in self.detector.feed(block, epoch):
                self._register_crack(crack)
        # Flush anything still queued after stop()
        if self.capture is not None:
            for block, epoch in self.capture.drain():
                for crack in self.detector.feed(block, epoch):
                    self._register_crack(crack)

    def _register_crack(self, crack):
        """Attach roast time and arming to a crack, feed the FC rule, alert."""
        elapsed = self._rule_elapsed(crack["epoch"])
        armed = (elapsed is not None and self.tracker.armed_at is not None
                 and elapsed >= self.tracker.armed_at)
        # Only a real CHARGE clock gives honest elapsed values for the sidecar
        honest = elapsed is not None and self.charge_epoch is not None
        crack["elapsed"] = round(elapsed, 1) if honest else None
        crack["armed"] = armed
        with self._lock:
            self.cracks.append(crack)
        if armed:
            fc = self.tracker.add(elapsed, crack["epoch"])
            if fc is not None:
                when = (f"T+{ear_display.fmt_time(fc['elapsed'])}" if self.charge_epoch is not None
                        else "now (no CHARGE clock)")
                print(f"  FC rule: {fc['count_in_window']} cracks in {fc['window_s']:.0f}s "
                      f"-> first crack at {when}")
                if not self.record_only:
                    shown = fc if self.charge_epoch is not None else {**fc, "elapsed": None}
                    alert.announce_fc(shown, self.tracker.cracks_per_minute(elapsed))

    def _elapsed(self):
        if self.charge_epoch is None:
            return None
        return time.time() - self.charge_epoch

    def _maybe_arm(self):
        """Fallback arming when DRY never arrives (or for bench runs)."""
        if self.tracker.armed_at is not None:
            return
        elapsed = self._elapsed()
        if elapsed is None:
            return
        if self.arm_at is not None:
            if elapsed >= self.arm_at:
                self.tracker.arm(self.arm_at, "manual")
        elif elapsed >= ARM_FALLBACK_S:
            self.tracker.arm(ARM_FALLBACK_S, f"CHARGE+{ARM_FALLBACK_S:.0f}")

    def stop_capture(self):
        """Stop recording and join the audio thread."""
        if self.capture is None or not self.capture.running:
            return
        self.capture.stop()
        if self._audio_thread is not None:
            self._audio_thread.join(timeout=5)
        print(f"  Recording stopped ({self.capture.duration_s():.0f}s)")

    # ---- sidecar, linking, pushing ----

    def _sidecar_path(self):
        return self.captures_dir / f"crack_{self.session_id}.json"

    def _link_alog(self):
        """Read the newest .alog Artisan just saved to get roastUUID/batch.

        Artisan writes the file on OFF, so retry briefly until it parses
        and its mtime has stopped changing.
        """
        save_dir = os.environ.get("ARTISAN_SAVE_DIR") or os.path.expanduser("~/coffee-roasts")
        save_path = Path(save_dir)
        if not save_path.exists():
            print(f"  Warning: Artisan save dir not found: {save_path}")
            return False
        # Only a file written during THIS session counts: on the first live
        # day the newest .alog at OFF time was still the previous roast's
        # (Artisan writes the new one a few seconds after sending OFF), and
        # every sidecar got linked one roast back.
        since = self.charge_epoch or (self.capture.start_epoch if self.capture else None) or time.time() - 3600
        # Artisan saves when the operator says so, not at OFF: batch #24 on
        # 2026-09-13 was saved 2.5 min after OFF (notes typed first) and a
        # 30 s poll missed it. Wait up to EAR_LINK_WAIT_S; Ctrl-C skips.
        wait_s = int(_env_float("EAR_LINK_WAIT_S", 300))
        last_mtime = None
        try:
            for i in range(wait_s):
                fresh = [p for p in save_path.glob("*.alog") if p.stat().st_mtime >= since]
                if fresh:
                    newest = max(fresh, key=lambda p: p.stat().st_mtime)
                    mtime = newest.stat().st_mtime
                    if mtime == last_mtime:
                        try:
                            raw = ast.literal_eval(newest.read_text(encoding="utf-8"))
                            self.roast_uuid = raw.get("roastUUID", "") or ""
                            self.batch_nr = raw.get("roastbatchnr", 0) or 0
                            print(f"  Linked to .alog: #{self.batch_nr} {raw.get('title', '')} "
                                  f"(UUID {self.roast_uuid[:8]}...)")
                            return True
                        except (ValueError, SyntaxError, OSError):
                            pass  # still being written; retry
                    last_mtime = mtime
                elif i and i % 30 == 0:
                    print(f"  Waiting for Artisan to save the .alog ({i}s; Ctrl-C to skip the link)")
                time.sleep(1)
        except KeyboardInterrupt:
            print("  Link skipped; sidecar stays unlinked (matched by date/time instead)")
            return False
        # Say what was there, so a failed link (batch #24, 2026-09-13) can be
        # diagnosed from the terminal afterwards
        stamp = lambda t: time.strftime("%H:%M:%S", time.localtime(t))
        existing = list(save_path.glob("*.alog"))
        if existing:
            newest = max(existing, key=lambda p: p.stat().st_mtime)
            print(f"  Warning: no .alog written since {stamp(since)}; newest in {save_path} is "
                  f"{newest.name} from {stamp(newest.stat().st_mtime)}. Not linked")
        else:
            print(f"  Warning: no .alog files in {save_path}; not linked")
        return False

    def _build_sidecar(self):
        """The sidecar dict (schema in CLAUDE.md, 'Ear' section)."""
        with self._lock:
            cracks = list(self.cracks)
        # With a provisional clock the rule's elapsed figures are relative to
        # a guessed CHARGE — leave them out; the epochs carry the real timing
        provisional = self.charge_epoch is None and self.rule_epoch is not None
        armed = None
        if self.tracker.armed_at is not None:
            armed = {"elapsed": None if provisional else round(self.tracker.armed_at, 1),
                     "source": self.tracker.armed_source}
        fc_detected = self.tracker.finalize()
        if fc_detected and provisional:
            fc_detected = dict(fc_detected)
            for key in ("elapsed", "first_crack_elapsed", "declared_at_elapsed", "peak_cpm_elapsed"):
                fc_detected[key] = None
            fc_detected["clock"] = "provisional"
        capture = None
        if self.capture is not None:
            stats = self.capture.stats()
            capture = {
                "device": self.device_name,
                "sample_rate": self.capture.sample_rate,
                "channels": 1,
                "dtype": "int16",
                "blocksize": self.capture.blocksize,
                "start_epoch": self.capture.start_epoch,
                "wav_file": self.wav_path.name if self.wav_path else "",
                "floor_db_at_arm": None,
                **stats,
            }
        return {
            "schema_version": SCHEMA_VERSION,
            "session_id": self.session_id,
            "bean_name": self.bean_name,
            "roast_uuid": self.roast_uuid,
            "batch_nr": self.batch_nr,
            "mode": self.mode,
            "artisan_events": {k.lower(): v for k, v in self.artisan.events.items()},
            "charge_epoch": self.charge_epoch,
            "charge_source": self.charge_source,
            "armed": armed,
            "capture": capture,
            "detector": dict(self.detector.params) if self.detector else dict(self.detector_params),
            "capture_error": self.capture_error,
            "fc_rule": self.tracker.rule(),
            "cracks": cracks,
            "fc_detected": fc_detected,
            "notes": self.notes,
        }

    def _save_sidecar(self):
        """Write the sidecar atomically; returns its path."""
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        path = self._sidecar_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._build_sidecar(), indent=2, default=str))
        os.replace(tmp, path)
        self._last_save = time.time()
        return path

    def _push(self, paths):
        """rsync files to CRACK_RSYNC_DEST (best effort, never raises)."""
        dest = os.environ.get("CRACK_RSYNC_DEST")
        if not dest:
            print("  Warning: CRACK_RSYNC_DEST not set, not pushing")
            return
        for p in paths:
            if p is None or not Path(p).exists():
                continue
            result = subprocess.run(["rsync", "-az", str(p), dest], capture_output=True)
            if result.returncode == 0:
                print(f"  Pushed {Path(p).name}")
            else:
                print(f"  Warning: rsync exit {result.returncode} pushing {Path(p).name}")

    def _push_wav_once(self):
        if self._wav_pushed or not self.push_wav or self.wav_path is None:
            return
        if self.capture is not None and self.capture.running:
            return  # header not written yet
        self._push([self.wav_path])
        self._wav_pushed = True

    def finalize(self):
        """Stop everything, link the .alog if OFF arrived, save and push. Idempotent."""
        if self._finalized:
            return
        self._finalized = True
        self.running = False
        self.stop_capture()
        # Save and push first, so the sidecar exists on the dev machine even
        # if the (possibly minutes-long) wait for Artisan's save is cut short
        path = self._save_sidecar()
        print(f"  Sidecar saved: {path}")
        self._push([path])
        self._push_wav_once()
        if self.off_received and self._link_alog():
            self._save_sidecar()
            self._push([path])

    # ---- display ----

    def _build_state(self):
        now = self._elapsed()
        with self._lock:
            total = len(self.cracks)
            total_armed = sum(1 for c in self.cracks if c.get("armed"))
            recent = sum(1 for c in self.cracks
                         if c.get("elapsed") is not None and now is not None
                         and c["elapsed"] > now - 60)
        rec = None
        if self.capture is not None:
            rec = {
                "file": self.wav_path.name if self.wav_path else "",
                "mb": self.capture.blocks * self.capture.blocksize * 2 / 1e6,
                "peak_dbfs": self.capture.peak_dbfs(),
                "overflows": self.capture.overflows,
                "clipped": self.capture.clipped_blocks,
                "duration_s": self.capture.duration_s(),
            }
        armed = None
        if self.tracker.armed_at is not None:
            armed = {"elapsed": self.tracker.armed_at, "source": self.tracker.armed_source}
        return {
            "bean_name": self.bean_name,
            "elapsed": now,
            "phase": self.artisan.current_phase,
            "connected": self.artisan.connected,
            "events": self.artisan.events,
            "recording": rec,
            "detector": {
                "floor_db": self.detector.floor_db() if self.detector else None,
                "level_db": self.detector.level() if self.detector else None,
                "armed": armed,
                "recent": recent,
                "total_armed": total_armed,
                "total": total,
            },
            "crack_status": self.tracker.status(now),
            "mode": self.mode + (" (alerts off)" if self.record_only else ""),
            "capture_error": self.capture_error,
        }

    # ---- main loop ----

    async def run(self):
        """Serve Artisan, record, detect; end at OFF, DROP timeout, or Ctrl-C."""
        self.running = True
        print(f"Ear session {self.session_id} for: {self.bean_name}  [{self.mode}]")
        print(f"WebSocket server on ws://0.0.0.0:{self.ws_port}/  (Artisan: 127.0.0.1:{self.ws_port} path WebSocket)")
        print(f"Device: {self.device_spec}   captures: {self.captures_dir}")
        await self.artisan.start()
        if self.record_now:
            try:
                self.start_capture()
            except Exception as e:
                self.capture_error = str(e)
                print(f"  !! capture failed: {e}")
        else:
            print("Waiting for Artisan to connect (press ON in Artisan)...")

        while self.running:
            self._maybe_arm()
            now = time.time()
            # Recording ends a little after DROP (cooling noise is useless)
            if self.drop_epoch is not None and self.capture is not None and self.capture.running \
                    and now - self.drop_epoch >= self.post_drop_s:
                self.stop_capture()
                self._push_wav_once()
            if self.off_received:
                break
            if self.drop_epoch is not None and now - self.drop_epoch >= self.off_timeout_s:
                print("  No OFF received; finishing on timeout")
                break
            # Crash guard: keep the sidecar fresh once recording has begun
            if self.capture is not None and now - self._last_save > 60:
                self._save_sidecar()
            ear_display.clear_and_render(self._build_state())
            await asyncio.sleep(1)

        self.finalize()
        ear_display.clear_and_render(self._build_state())
        self.artisan.stop()
        await self.artisan.wait_until_stopped()


def run_session(**kwargs):
    """Blocking entry point; Ctrl-C saves and pushes whatever exists."""
    session = EarSession(**kwargs)
    try:
        asyncio.run(session.run())
    except KeyboardInterrupt:
        print("\nEar interrupted — saving")
        session.finalize()
    return session


def load_latest_sidecar(captures_dir=None):
    """Newest crack_*.json in the captures dir, parsed, or None."""
    d = Path(captures_dir or os.path.expanduser(
        os.environ.get("EAR_CAPTURES_DIR", str(Path(__file__).parent / "captures"))))
    files = sorted(d.glob("crack_*.json"))
    if not files:
        return None
    return json.loads(files[-1].read_text())
