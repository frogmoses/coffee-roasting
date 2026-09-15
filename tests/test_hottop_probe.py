"""hottop_probe: frame codec parity with Artisan's hottop.py (no serial port needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ear"))

import hottop_probe as hp


def _machine_frame(bt_c=150, et_c=200, heater=60, main_fan=3, cool_fan=0, motor=1, sol=0, stir=0, chaff=0):
    """A reply frame laid out the way Artisan's read_msg decodes it."""
    f = bytearray(36)
    f[0:2] = hp.HEADER
    f[4] = 1
    f[10] = heater
    f[11] = cool_fan
    f[12] = main_fan
    f[16] = sol
    f[17] = motor
    f[18] = stir
    f[19] = chaff
    f[23], f[24] = divmod(et_c, 256)
    f[25], f[26] = divmod(bt_c, 256)
    f[35] = sum(f[:35]) & 0xFF
    return bytes(f)


def test_decode_reads_the_same_fields_as_artisan():
    dec = hp.decode(_machine_frame(bt_c=300, et_c=1 * 256 + 4, heater=85, main_fan=4, motor=1, sol=1))
    assert dec["bt_c"] == 300 and dec["et_c"] == 260
    assert dec["heater"] == 85 and dec["main_fan"] == 4 and dec["drum_motor"] == 1 and dec["solenoid"] == 1
    assert hp.c_to_f(300) == 572
    assert len(dec["unknown"]) == len(hp.UNKNOWN_BYTES) and 10 not in hp.UNKNOWN_BYTES


def test_control_frame_matches_artisan_create_msg():
    frame = hp.build_control(heater=35, cool_fan=0, main_fan=10, solenoid=0, drum_motor=1, stirrer=0)
    assert frame[:7] == b"\xa5\x96\xb0\xa0\x01\x01\x24"
    assert frame[10] == 35 and frame[11] == 0 and frame[12] == 10
    assert frame[16] == 0 and frame[17] == 1 and frame[18] == 0
    assert hp.checksum_ok(frame)
    # clamped like Artisan (heater 0-100, fans 0-10)
    clamped = hp.build_control(heater=140, cool_fan=99, main_fan=-3, solenoid=1, drum_motor=1, stirrer=1)
    assert clamped[10] == 100 and clamped[11] == 10 and clamped[12] == 0


def test_extract_frames_resyncs_on_junk_and_bad_checksums():
    good = _machine_frame()
    bad = bytearray(good)
    bad[35] ^= 0xFF
    stream = b"\x00\x01" + bytes(bad) + good + good[:20]
    frames, bad_count, rest = hp.extract_frames(stream)
    assert frames == [good] and bad_count == 1
    assert rest == good[:20]                     # partial frame kept for the next read
    frames, bad_count, rest = hp.extract_frames(rest + good[20:])
    assert frames == [good] and rest == b""
    assert hp.extract_frames(b"\x12\xa5") == ([], 0, b"\xa5")   # trailing half header kept
