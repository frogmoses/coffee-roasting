#!/usr/bin/env python3
"""Talk to the Hottop KN-8828B-2K+ directly, without Artisan.

Speaks the same 36-byte USB-serial protocol as Artisan's hottop driver
(artisanlib/hottop.py), so a control frame here is byte-for-byte what
Artisan would send. Purpose: find out what the machine's firmware accepts
in each of its states (ready screen, preheat, roasting, cooling countdown,
after an eject) without Artisan's own connection/control state in the way.

    run_ear hottop_probe.py                      # /dev/ttyUSB0, watch only
    run_ear hottop_probe.py --port /dev/ttyUSB1
    run_ear hottop_probe.py --max-bt-f 300       # heater forced to 0 above this

Every frame received is decoded on screen when something changes and every
frame (both directions) is appended to a log file in EAR_CAPTURES_DIR
(hottop_probe_YYYY-MM-DD_HHMM.log), one line per frame with the raw hex, so
the bytes Artisan does not decode can be studied afterwards for a
state/mode byte.

Commands (type at the prompt, Enter):
    c         toggle control: start/stop answering every machine frame with a
              control frame (Artisan's behaviour while in Hottop control)
    h N       heater 0-100          (only sent while control is on)
    f N       main fan 0-10         (drum fan; Artisan's fan(N))
    cf N      cooling fan 0-10      (bean-cooling tray fan)
    m 0|1     drum motor
    s 0|1     solenoid (eject door)
    st 0|1    stirrer (cooling-tray motor)
    e         echo: forget all commanded values, send back what the machine reports
    drop      Artisan's DROP button:     heater 0, fan 10, motor 1, solenoid 1, stirrer 1
    coolend   Artisan's COOL END button: heater 0, fan 0,  motor 0, solenoid 0, stirrer 0
    raw       toggle printing every frame's raw hex
    ?         status now
    q         heater 0, control off, quit (Ctrl-C does the same)

Safety: while control is on the heater is forced to 0 whenever BT is at or
above --max-bt-f (default 300F; Artisan's own cut-off is 428F). Run this
with an EMPTY drum. The machine ejects when it hears nothing for a second
after control started; that is expected behaviour to observe, not a fault.
"""
import argparse
import datetime as _dt
import os
import sys
import threading
import time

HEADER = b"\xa5\x96"
FRAME_LEN = 36
SEND_INTERVAL = 0.3     # Artisan's minimum spacing between control frames
DEFAULT_PORT = "/dev/ttyUSB0"
BAUD = 115200

# Byte positions Artisan decodes (hottop.py read_msg) -- everything else is unknown
KNOWN = {4: "version", 10: "heater", 11: "cool_fan", 12: "main_fan", 16: "solenoid",
         17: "drum_motor", 18: "stirrer", 19: "chaff_tray", 23: "et_hi", 24: "et_lo",
         25: "bt_hi", 26: "bt_lo", 35: "checksum"}
UNKNOWN_BYTES = [i for i in range(2, FRAME_LEN) if i not in KNOWN]


def c_to_f(c):
    """Celsius -> Fahrenheit, rounded to a whole degree."""
    return int(round(c * 9.0 / 5.0 + 32))


def checksum_ok(frame):
    """Artisan's CRC: low byte of the sum of the first 35 bytes."""
    return len(frame) == FRAME_LEN and (sum(frame[:35]) & 0xFF) == frame[35]


def decode(frame):
    """Decode the fields Artisan reads; temperatures are in C as the machine sends them."""
    return {
        "version": frame[4],
        "heater": frame[10],
        "cool_fan": frame[11],
        "main_fan": frame[12],
        "solenoid": frame[16],
        "drum_motor": frame[17],
        "stirrer": frame[18],
        "chaff_tray": frame[19],
        "et_c": frame[23] * 256 + frame[24],
        "bt_c": frame[25] * 256 + frame[26],
        "unknown": bytes(frame[i] for i in UNKNOWN_BYTES),
    }


def build_control(heater, cool_fan, main_fan, solenoid, drum_motor, stirrer):
    """Build the control frame exactly as Artisan's create_msg does."""
    cmd = bytearray(FRAME_LEN)
    cmd[0:2] = HEADER
    cmd[2] = 0xB0
    cmd[3] = 0xA0
    cmd[4] = 0x01
    cmd[5] = 0x01
    cmd[6] = 0x24
    cmd[10] = max(0, min(int(heater), 100))
    cmd[11] = max(0, min(int(cool_fan), 10))
    cmd[12] = max(0, min(int(main_fan), 10))
    cmd[16] = 1 if solenoid else 0
    cmd[17] = 1 if drum_motor else 0
    cmd[18] = 1 if stirrer else 0
    cmd[35] = sum(cmd[:35]) & 0xFF
    return bytes(cmd)


def extract_frames(buf):
    """Pull complete, checksum-valid frames out of a byte buffer.

    Returns (frames, bad_count, remaining_buffer). Bytes before a header are
    dropped; a frame that fails the checksum is counted in bad_count and the
    scan resumes just past its header.
    """
    frames, bad = [], 0
    while True:
        start = buf.find(HEADER)
        if start < 0:
            # keep a trailing 0xA5 in case the 0x96 is still in flight
            keep = buf[-1:] if buf.endswith(HEADER[:1]) else b""
            return frames, bad, keep
        if len(buf) - start < FRAME_LEN:
            return frames, bad, buf[start:]
        frame = buf[start:start + FRAME_LEN]
        if checksum_ok(frame):
            frames.append(frame)
            buf = buf[start + FRAME_LEN:]
        else:
            bad += 1
            buf = buf[start + 2:]


class Probe:
    """Serial reader/writer plus the commanded-value state (Artisan's _set_* fields)."""

    def __init__(self, port, log_path, max_bt_f):
        self.port_name = port
        self.log_path = log_path
        self.max_bt_c = (max_bt_f - 32) * 5.0 / 9.0
        self.control = False
        self.raw = False
        # -1 = not commanded: echo the machine's own value, like Artisan
        self.set_values = dict(heater=-1, cool_fan=-1, main_fan=-1, solenoid=-1, drum_motor=-1, stirrer=-1)
        self.last = None            # last decoded frame
        self.last_rx = None         # wall time of the last valid frame
        self.last_tx = 0.0
        self.rx_count = 0
        self.tx_count = 0
        self.bad_count = 0
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.log = open(log_path, "a", encoding="utf-8")
        self.ser = None

    # ---- serial -------------------------------------------------------------
    def open(self):
        import serial   # pyserial; imported here so the codec is testable without it
        self.ser = serial.Serial(self.port_name, BAUD, bytesize=8, parity="N", stopbits=1, timeout=0.3)
        self.say(f"opened {self.port_name} @ {BAUD}; log {self.log_path}")

    def reader(self):
        """Read frames forever; answer each one with a control frame while control is on."""
        buf = b""
        while not self.stop_event.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as e:      # pylint: disable=broad-except
                self.say(f"serial read failed: {e}")
                self.stop_event.set()
                return
            if not chunk:
                if self.last_rx and time.time() - self.last_rx > 3 and self.rx_count:
                    self.say(f"no frame for {time.time() - self.last_rx:.0f}s", once="silent")
                continue
            buf += chunk
            frames, bad, buf = extract_frames(buf)
            self.bad_count += bad
            for frame in frames:
                self.on_frame(frame)

    def on_frame(self, frame):
        now = time.time()
        self.rx_count += 1
        self.last_rx = now
        dec = decode(frame)
        self.log_line("RX", frame, dec)
        changed = self.last is None or any(dec[k] != self.last[k] for k in dec)
        if self.raw:
            print(f"  rx {frame.hex(' ')}")
        if changed:
            self.show(dec, now)
        self.last = dec
        if self.control and now > self.last_tx + SEND_INTERVAL:
            self.send_control(dec)

    def send_control(self, dec):
        """Send one control frame: commanded values where set, the machine's own otherwise."""
        with self.lock:
            v = {k: (self.set_values[k] if self.set_values[k] != -1 else dec[k]) for k in self.set_values}
            if dec["bt_c"] >= self.max_bt_c and v["heater"] > 0:
                self.say(f"BT {c_to_f(dec['bt_c'])}F >= ceiling: heater forced to 0")
                self.set_values["heater"] = 0
                v["heater"] = 0
        frame = build_control(v["heater"], v["cool_fan"], v["main_fan"], v["solenoid"], v["drum_motor"], v["stirrer"])
        try:
            self.ser.write(frame)
        except Exception as e:      # pylint: disable=broad-except
            self.say(f"serial write failed: {e}")
            return
        self.tx_count += 1
        self.last_tx = time.time()
        self.log_line("TX", frame, None)
        if self.raw:
            print(f"  tx {frame.hex(' ')}")

    # ---- output -------------------------------------------------------------
    def stamp(self):
        return _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def say(self, msg, once=None):
        # 'once' keys suppress repeats of the same notice until something else prints
        if once and getattr(self, "_last_once", None) == once:
            return
        self._last_once = once
        print(f"{self.stamp()} {msg}")
        self.log.write(f"{self.stamp()} NOTE {msg}\n")
        self.log.flush()

    def show(self, dec, now):
        self._last_once = None
        ctl = "CTL" if self.control else "---"
        line = (f"{self.stamp()} {ctl} BT {c_to_f(dec['bt_c']):3d}F ET {c_to_f(dec['et_c']):3d}F "
                f"heat {dec['heater']:3d}% fan {dec['main_fan']:2d} cfan {dec['cool_fan']:2d} "
                f"motor {dec['drum_motor']} sol {dec['solenoid']} stir {dec['stirrer']} chaff {dec['chaff_tray']} "
                f"| unk {dec['unknown'].hex(' ')}")
        if self.last is not None and dec["unknown"] != self.last["unknown"]:
            diffs = [f"b{UNKNOWN_BYTES[i]}:{self.last['unknown'][i]:02x}->{b:02x}"
                     for i, b in enumerate(dec["unknown"]) if b != self.last["unknown"][i]]
            line += "  CHANGED " + " ".join(diffs)
        print(line)

    def status(self):
        age = f"{time.time() - self.last_rx:.1f}s ago" if self.last_rx else "never"
        cmd = ", ".join(f"{k}={v}" for k, v in self.set_values.items() if v != -1) or "echo"
        self.say(f"control={'on' if self.control else 'off'} commanded[{cmd}] rx={self.rx_count} "
                 f"tx={self.tx_count} bad={self.bad_count} last frame {age}")
        if self.last:
            self.show(self.last, time.time())

    def log_line(self, direction, frame, dec):
        if dec:
            fields = (f"bt_c={dec['bt_c']} et_c={dec['et_c']} heater={dec['heater']} main_fan={dec['main_fan']} "
                      f"cool_fan={dec['cool_fan']} motor={dec['drum_motor']} sol={dec['solenoid']} "
                      f"stir={dec['stirrer']} chaff={dec['chaff_tray']}")
        else:
            fields = "control=" + ("on" if self.control else "off")
        self.log.write(f"{_dt.datetime.now().isoformat(timespec='milliseconds')} {direction} {frame.hex()} {fields}\n")
        self.log.flush()

    # ---- commands -----------------------------------------------------------
    def apply(self, **kw):
        with self.lock:
            self.set_values.update(kw)
        self.say("commanded " + ", ".join(f"{k}={v}" for k, v in kw.items())
                 + ("" if self.control else "  (control is off: nothing is sent until 'c')"))

    def command(self, text):
        parts = text.strip().split()
        if not parts:
            return True
        op, arg = parts[0].lower(), (parts[1] if len(parts) > 1 else None)
        try:
            if op == "q":
                return False
            if op == "?":
                self.status()
            elif op == "c":
                self.control = not self.control
                self.say(f"control {'ON: answering every frame' if self.control else 'OFF: sending nothing'}")
            elif op == "raw":
                self.raw = not self.raw
            elif op == "e":
                self.apply(heater=-1, cool_fan=-1, main_fan=-1, solenoid=-1, drum_motor=-1, stirrer=-1)
            elif op == "h":
                self.apply(heater=int(arg))
            elif op == "f":
                self.apply(main_fan=int(arg))
            elif op == "cf":
                self.apply(cool_fan=int(arg))
            elif op == "m":
                self.apply(drum_motor=int(arg))
            elif op == "s":
                self.apply(solenoid=int(arg))
            elif op == "st":
                self.apply(stirrer=int(arg))
            elif op == "drop":
                self.apply(heater=0, main_fan=10, drum_motor=1, solenoid=1, stirrer=1)
            elif op == "coolend":
                self.apply(heater=0, main_fan=0, drum_motor=0, solenoid=0, stirrer=0)
            else:
                print("unknown command; see the docstring (c h f cf m s st e drop coolend raw ? q)")
        except (TypeError, ValueError):
            print(f"'{op}' needs a number")
        return True

    def shutdown(self):
        if self.control and self.ser is not None and self.last is not None:
            with self.lock:
                self.set_values["heater"] = 0
            self.send_control(self.last)
            self.say("heater 0 sent; control off")
        self.control = False
        self.stop_event.set()
        try:
            if self.ser is not None:
                self.ser.close()
        finally:
            self.log.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hottop 2K+ serial probe (Artisan protocol, no Artisan)")
    ap.add_argument("--port", default=os.environ.get("HOTTOP_PORT", DEFAULT_PORT))
    ap.add_argument("--max-bt-f", type=float, default=300.0, help="heater forced to 0 at/above this BT (default 300F)")
    ap.add_argument("--log-dir", default=os.path.expanduser(os.environ.get("EAR_CAPTURES_DIR", "captures")))
    args = ap.parse_args(argv)

    os.makedirs(args.log_dir, exist_ok=True)
    log_path = os.path.join(args.log_dir, _dt.datetime.now().strftime("hottop_probe_%Y-%m-%d_%H%M.log"))
    probe = Probe(args.port, log_path, args.max_bt_f)
    try:
        probe.open()
    except Exception as e:      # pylint: disable=broad-except
        print(f"cannot open {args.port}: {e}")
        return 1
    t = threading.Thread(target=probe.reader, daemon=True)
    t.start()
    print("watching (control off). 'c' starts control frames, '?' status, 'q' quits.")
    try:
        while not probe.stop_event.is_set():
            try:
                line = input("> ")
            except EOFError:
                break
            if not probe.command(line):
                break
    except KeyboardInterrupt:
        print()
    finally:
        probe.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
