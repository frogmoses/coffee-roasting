"""Live terminal status for an ear session (box-drawing, 62 wide).

Trimmed from gopro/sentinel_display.py: connection and roast clock, events,
recording level, detector floor/level/arming, crack counts, and the first
crack badge once declared.
"""

import sys

H_LINE = "─"
V_LINE = "│"
TL_CORNER = "┌"
TR_CORNER = "┐"
BL_CORNER = "└"
BR_CORNER = "┘"
T_RIGHT = "├"
T_LEFT = "┤"
WIDTH = 62


def fmt_time(seconds):
    """Format seconds as M:SS ('--:--' for None)."""
    if seconds is None:
        return "--:--"
    m = int(seconds) // 60
    return f"{m}:{int(seconds) % 60:02d}"


def _header(title):
    return f"{TL_CORNER}{H_LINE} {title} {H_LINE * (WIDTH - len(title) - 4)}{TR_CORNER}"


def _footer():
    return f"{BL_CORNER}{H_LINE * (WIDTH - 2)}{BR_CORNER}"


def _sep():
    return f"{T_RIGHT}{H_LINE * (WIDTH - 2)}{T_LEFT}"


def _row(left, right=""):
    content = f" {left}"
    if right:
        pad = WIDTH - len(content) - len(str(right)) - 3
        content = f" {left}{' ' * max(pad, 1)}{right}"
    return f"{V_LINE}{content}{' ' * max(WIDTH - len(content) - 2, 0)} {V_LINE}"


def _db(v):
    return "--" if v is None else f"{v:.0f} dB"


def render_status(state):
    """Render the session state dict (see EarSession._build_state) as lines."""
    lines = [_header(f"Ear: {state.get('bean_name', '')}")]
    conn = "connected" if state.get("connected") else "waiting for Artisan (press ON)"
    phase = state.get("phase") or "-"
    lines.append(_row(f"Artisan: {conn}", f"T+{fmt_time(state.get('elapsed'))}  {phase}"))
    events = state.get("events") or {}
    if events:
        ev = "  ".join(f"{k} {fmt_time(v)}" for k, v in events.items())
        lines.append(_row(f"Events: {ev}"[:WIDTH - 4]))
    lines.append(_sep())

    rec = state.get("recording") or {}
    if rec:
        lines.append(_row(f"Rec: {rec.get('file', '-')}", f"{rec.get('mb', 0):.0f} MB"))
        ov = rec.get("overflows", 0)
        clip = rec.get("clipped", 0)
        warn = "  !! CLIPPING" if clip else ("  !! overflows" if ov else "")
        peak = rec.get("peak_dbfs")
        lines.append(_row(f"Peak {'--' if peak is None else f'{peak:.0f} dBFS'}{warn}",
                          f"{fmt_time(rec.get('duration_s'))} recorded"))
    elif state.get("capture_error"):
        lines.append(_row(f"Rec: FAILED {state['capture_error']}"[:WIDTH - 4]))
    else:
        lines.append(_row("Rec: not started"))

    det = state.get("detector") or {}
    armed = det.get("armed")
    armed_txt = f"armed {armed['source']} @ {fmt_time(armed['elapsed'])}" if armed else "not armed"
    lines.append(_row(f"Floor {_db(det.get('floor_db'))}   Level {_db(det.get('level_db'))}", armed_txt))
    lines.append(_row(f"Cracks: last 60s {det.get('recent', 0)}   armed total {det.get('total_armed', 0)}",
                      f"all {det.get('total', 0)}"))

    badge = state.get("crack_status")
    if badge:
        lines.append(_sep())
        lines.append(_row(f"** FIRST CRACK at T+{fmt_time(badge['elapsed_seconds'])}",
                          f"{badge['cracks_per_minute']:.0f}/min"))
    mode = state.get("mode", "")
    lines.append(_sep())
    lines.append(_row(f"Mode: {mode}", "Ctrl-C saves and exits"))
    lines.append(_footer())
    return "\n".join(lines)


def clear_and_render(state):
    """Clear the terminal and draw the status box."""
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.write(render_status(state) + "\n")
    sys.stdout.flush()
