"""Operator alert when first crack is declared.

Terminal line + bell always; an optional sound file via paplay (PipeWire's
Pulse shim) falling back to aplay, spawned so the session loop never blocks.
"""

import os
import shutil
import subprocess


def _fmt(seconds):
    m = int(seconds) // 60
    return f"{m}:{int(seconds) % 60:02d}"


def announce_fc(fc, cpm=None, sound_path=None):
    """Announce a declared first crack.

    Args:
        fc: fc_detected dict from FirstCrackTracker.
        cpm: current cracks per minute, for the line.
        sound_path: optional audio file to play; defaults to EAR_ALERT_SOUND.
    """
    rate = f" ({cpm:.0f}/min)" if cpm else ""
    print(f"\a\033[1m>>> FIRST CRACK {_fmt(fc['elapsed'])}{rate} <<<\033[0m", flush=True)
    path = sound_path or os.environ.get("EAR_ALERT_SOUND", "")
    if not path or not os.path.exists(path):
        return
    player = shutil.which("paplay") or shutil.which("aplay")
    if not player:
        return
    try:
        subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass
