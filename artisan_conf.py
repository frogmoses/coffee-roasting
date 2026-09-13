r"""Read and write Artisan's settings file (a Qt QSettings INI).

Artisan keeps every setting in one file — on the roaster
`~/.config/artisan-scope/Artisan.conf` — and reads it fresh at every start.
The copy under `artisan/Artisan.conf` in this repo is the reviewed truth;
`artisan/deploy.sh pull|diff|push` moves it to and from the roaster.

The file is line-oriented: `[Section]` headers and `key=value` lines. This
module keeps every line byte-for-byte unless it is asked to change that key,
so untouched settings (including the opaque `@ByteArray(...)`/`@Variant(...)`
values Qt serialises for geometry and the like) survive a round trip
unchanged. Only values it parses or formats follow Qt's INI escaping:

- a list is written `a, b, c` (comma-space); an element that contains `,`,
  `;`, `=`, or leading/trailing space is wrapped in double quotes
- inside a value `\\` `\"` `\n` `\r` `\t` are escaped, other control
  characters as `\xHH`; the escapes apply whether or not the element is
  quoted (Artisan writes `send({\"event\":\"CHARGE\"})` unquoted)
- a value starting with `@` is opaque and is never parsed

The action-code tables mirror Artisan 4.0 (`artisanlib/events.py`,
`artisanlib/alarms.py`): each kind of control stores a different index into
a different combobox list, so the same "Hottop Command" is 8 on a default
button, 10 on a custom button, and 7 on a slider.

Usage (add -f FILE for a file other than artisan/Artisan.conf):
    python artisan_conf.py show                # sections and keys
    python artisan_conf.py show buttons        # custom event buttons, decoded
    python artisan_conf.py show default-buttons
    python artisan_conf.py show sliders
    python artisan_conf.py show alarms
    python artisan_conf.py get SECTION KEY
    python artisan_conf.py set SECTION KEY VALUE [VALUE ...]   # >1 value = list
    python artisan_conf.py check               # parse/format round trip
"""

import argparse
import sys
from pathlib import Path

DEFAULT_CONF = Path(__file__).parent / "artisan" / "Artisan.conf"

# ---------------------------------------------------------------- code tables

# [DefaultButtons] buttonactions / extrabuttonactions: index into
# events.py `buttonActionTypes` (CHARGE, DRY, FCs, FCe, SCs, SCe, DROP,
# COOL END; and ON, OFF, SAMPLING).
DEFAULT_BUTTON_ACTIONS = [
    "", "Serial Command", "Call Program", "Modbus Command", "DTA Command",
    "IO Command", "Hottop Heater", "Hottop Fan", "Hottop Command", "p-i-d",
    "Fuji Command", "PWM Command", "VOUT Command", "S7 Command",
    "Aillio R1 Heater", "Aillio R1 Fan", "Aillio R1 Drum", "Aillio R1 Command",
    "Artisan Command", "RC Command", "Multiple Event", "WebSocket Command",
]
DEFAULT_BUTTON_NAMES = ["CHARGE", "DRY", "FCs", "FCe", "SCs", "SCe", "DROP", "COOL END"]
EXTRA_BUTTON_NAMES = ["ON", "OFF", "SAMPLING"]

# [ExtraEventButtons] extraeventsactions: Artisan's canonical action code,
# which is the index into `custom_button_actions` plus one above 6 (code 7,
# "Call Program with argument", is not offered on buttons).
_CUSTOM_LIST = [
    "", "Serial Command", "Call Program", "Multiple Event", "Modbus Command",
    "DTA Command", "IO Command", "Hottop Heater", "Hottop Fan", "Hottop Command",
    "p-i-d", "Fuji Command", "PWM Command", "VOUT Command", "S7 Command",
    "Aillio R1 Heater", "Aillio R1 Fan", "Aillio R1 Drum", "Aillio R1 Command",
    "Artisan Command", "RC Command", "WebSocket Command", "Stepper Command",
]
CUSTOM_BUTTON_ACTIONS = {}
for _i, _name in enumerate(_CUSTOM_LIST):
    CUSTOM_BUTTON_ACTIONS[_i + 1 if _i > 6 else _i] = _name
CUSTOM_BUTTON_ACTIONS[7] = "Call Program (with argument)"

# [Sliders] slideractions: index into `sliderActionTypes`.
SLIDER_ACTIONS = [
    "", "Serial Command", "Modbus Command", "DTA Command", "Call Program",
    "Hottop Heater", "Hottop Fan", "Hottop Command", "Fuji Command",
    "PWM Command", "VOUT Command", "IO Command", "S7 Command",
    "Aillio R1 Heater", "Aillio R1 Fan", "Aillio R1 Drum", "Artisan Command",
    "RC Command", "WebSocket Command", "Stepper Command",
]

# extraeventstypes: the four event types (this machine logs them as Fan,
# Drum, Damper, Heater — Artisan's defaults are Air/Drum/Damper/Burner),
# 4 = none, 5-8 = the same four as relative (±) moves.
EVENT_TYPES = ["Fan", "Drum", "Damper", "Heater", "--", "±Fan", "±Drum", "±Damper", "±Heater"]

# Alarm encodings (alarms.py export/import). In the settings file the table
# lives under [Alarms] as parallel lists: alarmflag, alarmguard,
# alarmnegguard, alarmtime (From), alarmoffset, alarmcond, alarmsource,
# alarmtemperature, alarmaction, alarmbeep, alarmstrings (+ alarmsetlabel).
ALARM_FROM = {9: "ON", -1: "START", 0: "CHARGE", 8: "TP", 1: "DRY END", 2: "FC START",
              3: "FC END", 4: "SC START", 5: "SC END", 6: "DROP", 7: "COOL END", 10: "If Alarm"}
ALARM_COND = {0: "<", 1: ">", 2: "=", 3: "≠"}
ALARM_SOURCE = {-3: "", -2: "ΔET", -1: "ΔBT", 0: "ET", 1: "BT"}   # 2+ = extra devices
ALARM_ACTIONS = {
    -1: "", 0: "Pop Up", 1: "Call Program", 2: "Event Button", 3: "Slider 1", 4: "Slider 2",
    5: "Slider 3", 6: "Slider 4", 7: "START", 8: "DRY END", 9: "FC START", 10: "FC END",
    11: "SC START", 12: "SC END", 13: "DROP", 14: "COOL END", 15: "OFF", 16: "CHARGE",
    17: "RampSoak ON", 18: "RampSoak OFF", 19: "PID ON", 20: "PID OFF", 21: "SV",
    22: "Playback ON", 23: "Playback OFF", 24: "Set Canvas Color", 25: "Reset Canvas Color",
}

# ------------------------------------------------------------ value encoding

_SIMPLE_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def parse_value(raw):
    """Decode a Qt INI value into a list of strings.

    A value without a top-level comma decodes to a one-element list; the
    caller decides whether it meant a string or a list (Artisan knows from
    the key). Opaque `@...` values raise ValueError.
    """
    if raw.startswith("@"):
        raise ValueError("opaque Qt value; leave it as-is")
    items = []
    current = []
    i, n = 0, len(raw)
    in_quotes = False
    while i < n:
        ch = raw[i]
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            i += 2
            if nxt == "x":
                j = i
                while j < n and raw[j] in "0123456789abcdefABCDEF":
                    j += 1
                if j > i:
                    current.append(chr(int(raw[i:j], 16)))
                    i = j
                else:
                    current.append("x")
            elif nxt in _SIMPLE_ESCAPES:
                current.append(_SIMPLE_ESCAPES[nxt])
            else:
                current.append(nxt)      # \\ \" \, \; and anything else literal
            continue
        if ch == '"':
            in_quotes = not in_quotes
            i += 1
            continue
        if ch == "," and not in_quotes:
            items.append("".join(current))
            current = []
            i += 1
            # Qt writes ", " and skips the blanks after a separator when reading
            while i < n and raw[i] == " ":
                i += 1
            continue
        current.append(ch)
        i += 1
    items.append("".join(current))
    return items


def _escape(text):
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20:
            out.append(f"\\x{ord(ch):x}")
        else:
            out.append(ch)
    return "".join(out)


def format_element(text):
    """One list element (or a whole scalar), quoted when Qt would quote it."""
    text = str(text)
    needs_quotes = (text != text.strip()) or any(c in text for c in ",;=")
    body = _escape(text)
    return f'"{body}"' if needs_quotes else body


def format_value(values):
    """Encode a list of strings (or one scalar) as a Qt INI value."""
    if isinstance(values, (str, int, float, bool)):
        values = [values]
    return ", ".join(format_element(v) for v in values)


# --------------------------------------------------------------- the file


class ArtisanConf:
    """Line-preserving view of an Artisan settings file."""

    def __init__(self, lines):
        self.lines = list(lines)

    @classmethod
    def load(cls, path=DEFAULT_CONF):
        text = Path(path).read_text(encoding="utf-8")
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()          # trailing newline; restored on save
        return cls(lines)

    def save(self, path=DEFAULT_CONF):
        Path(path).write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    # -- navigation

    def _walk(self):
        """Yield (line_index, section, key) for every key line."""
        section = ""
        for i, line in enumerate(self.lines):
            if line.startswith("[") and line.rstrip().endswith("]"):
                section = line.strip()[1:-1]
            elif "=" in line and not line.lstrip().startswith((";", "#")):
                yield i, section, line.split("=", 1)[0]

    def sections(self):
        seen = []
        for line in self.lines:
            if line.startswith("[") and line.rstrip().endswith("]"):
                seen.append(line.strip()[1:-1])
        return seen

    def keys(self, section):
        return [k for _, s, k in self._walk() if s == section]

    def _find(self, section, key):
        for i, s, k in self._walk():
            if s == section and k == key:
                return i
        return None

    # -- raw access

    def get(self, section, key, default=None):
        """The raw value string, exactly as in the file."""
        i = self._find(section, key)
        return default if i is None else self.lines[i].split("=", 1)[1]

    def set(self, section, key, raw):
        """Write a raw value string; adds the key (and section) if missing."""
        i = self._find(section, key)
        if i is not None:
            self.lines[i] = f"{key}={raw}"
            return
        # Append inside the section: after its last key line
        last = None
        for j, s, _ in self._walk():
            if s == section:
                last = j
        if last is not None:
            self.lines.insert(last + 1, f"{key}={raw}")
            return
        if section not in self.sections():
            if self.lines and self.lines[-1] != "":
                self.lines.append("")
            self.lines.append(f"[{section}]")
        self.lines.append(f"{key}={raw}")

    def delete(self, section, key):
        i = self._find(section, key)
        if i is not None:
            del self.lines[i]

    # -- decoded access

    def get_list(self, section, key):
        raw = self.get(section, key)
        return None if raw is None else parse_value(raw)

    def get_str(self, section, key, default=None):
        items = self.get_list(section, key)
        return default if items is None else items[0]

    def set_list(self, section, key, values):
        self.set(section, key, format_value(values))

    # -- round trip

    def roundtrip_mismatches(self):
        """(section, key) pairs whose value would change if re-encoded."""
        bad = []
        for i, s, k in self._walk():
            raw = self.lines[i].split("=", 1)[1]
            if raw.startswith("@"):
                continue
            if format_value(parse_value(raw)) != raw:
                bad.append((s, k))
        return bad

    # -- decoded views of the controls

    def default_buttons(self):
        actions = self.get_list("DefaultButtons", "buttonactions") or []
        cmds = self.get_list("DefaultButtons", "buttonactionstrings") or []
        vis = self.get_list("DefaultButtons", "buttonvisibility") or []
        rows = []
        for i, name in enumerate(DEFAULT_BUTTON_NAMES):
            code = int(actions[i]) if i < len(actions) else 0
            rows.append({"button": name, "action": _label(DEFAULT_BUTTON_ACTIONS, code),
                         "code": code, "command": cmds[i] if i < len(cmds) else "",
                         "visible": (vis[i] if i < len(vis) else "") == "true"})
        xactions = self.get_list("DefaultButtons", "extrabuttonactions") or []
        xcmds = self.get_list("DefaultButtons", "extrabuttonactionstrings") or []
        for i, name in enumerate(EXTRA_BUTTON_NAMES):
            code = int(xactions[i]) if i < len(xactions) else 0
            rows.append({"button": name, "action": _label(DEFAULT_BUTTON_ACTIONS, code),
                         "code": code, "command": xcmds[i] if i < len(xcmds) else "",
                         "visible": True})
        return rows

    def custom_buttons(self):
        sec = "ExtraEventButtons"
        cols = {name: self.get_list(sec, key) or [] for name, key in (
            ("label", "extraeventslabels"), ("description", "extraeventsdescriptions"),
            ("type", "extraeventstypes"), ("value", "extraeventsvalues"),
            ("action", "extraeventsactions"), ("command", "extraeventsactionstrings"),
            ("visible", "extraeventsvisibility"), ("color", "extraeventbuttoncolor"),
            ("textcolor", "extraeventbuttontextcolor"),
        )}
        n = max((len(v) for v in cols.values()), default=0)
        rows = []
        for i in range(n):
            cell = {name: (col[i] if i < len(col) else "") for name, col in cols.items()}
            code = int(cell["action"] or 0)
            etype = int(cell["type"] or 4)
            rows.append({
                "index": i + 1,
                "label": cell["label"].replace("\n", " "),
                "description": cell["description"],
                "event_type": EVENT_TYPES[etype] if 0 <= etype < len(EVENT_TYPES) else str(etype),
                "value": cell["value"],
                "action": CUSTOM_BUTTON_ACTIONS.get(code, str(code)),
                "code": code,
                "command": cell["command"],
                "visible": cell["visible"] == "1",
                "color": cell["color"],
            })
        return rows

    def sliders(self):
        actions = self.get_list("Sliders", "slideractions") or []
        vis = self.get_list("Sliders", "slidervisibilities") or []
        rows = []
        for i in range(4):
            code = int(actions[i]) if i < len(actions) else 0
            rows.append({"slider": i + 1, "event_type": EVENT_TYPES[i],
                         "action": _label(SLIDER_ACTIONS, code), "code": code,
                         "visible": (vis[i] if i < len(vis) else "0") == "1"})
        return rows

    def alarms(self):
        """Decoded alarm table (the [Alarms] section), or [] when none."""
        sec = "Alarms"
        cols = {name: self.get_list(sec, key) or [] for name, key in (
            ("flag", "alarmflag"), ("guard", "alarmguard"), ("negguard", "alarmnegguard"),
            ("from", "alarmtime"), ("offset", "alarmoffset"), ("cond", "alarmcond"),
            ("source", "alarmsource"), ("temperature", "alarmtemperature"),
            ("action", "alarmaction"), ("beep", "alarmbeep"), ("string", "alarmstrings"),
        )}
        n = max((len(v) for v in cols.values()), default=0)
        rows = []
        for i in range(n):
            cell = {name: (col[i] if i < len(col) else "") for name, col in cols.items()}
            src = int(cell["source"] or -3)
            rows.append({
                "index": i + 1, "enabled": cell["flag"] == "1",
                "guard": int(cell["guard"] or -1), "negguard": int(cell["negguard"] or -1),
                "from": ALARM_FROM.get(int(cell["from"] or 0), cell["from"]),
                "offset_s": int(float(cell["offset"] or 0)),
                "source": ALARM_SOURCE.get(src, f"extra {src - 2}"),
                "cond": ALARM_COND.get(int(cell["cond"] or 0), cell["cond"]),
                "temperature": cell["temperature"],
                "action": ALARM_ACTIONS.get(int(cell["action"] or -1), cell["action"]),
                "beep": cell["beep"] == "1", "string": cell["string"],
            })
        return rows


def _label(table, code):
    return table[code] if 0 <= code < len(table) else f"? ({code})"


# ------------------------------------------------------------------- CLI


def _print_rows(rows, columns):
    if not rows:
        print("  (none)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in columns}
    print("  " + "  ".join(c.ljust(widths[c]) for c in columns))
    for r in rows:
        print("  " + "  ".join(str(r.get(c, "")).ljust(widths[c]) for c in columns))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("-f", "--file", default=str(DEFAULT_CONF), help="settings file (default artisan/Artisan.conf)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_show = sub.add_parser("show", help="decoded views")
    p_show.add_argument("what", nargs="?", default="sections",
                        choices=["sections", "buttons", "default-buttons", "sliders", "alarms"])
    p_get = sub.add_parser("get", help="raw value of one key")
    p_get.add_argument("section"); p_get.add_argument("key")
    p_set = sub.add_parser("set", help="set a key (several values make a list)")
    p_set.add_argument("section"); p_set.add_argument("key"); p_set.add_argument("values", nargs="+")
    sub.add_parser("check", help="parse/format round trip over every key")
    args = ap.parse_args(argv)

    conf = ArtisanConf.load(args.file)
    if args.cmd == "show":
        if args.what == "sections":
            for s in conf.sections():
                print(f"[{s}]  {len(conf.keys(s))} keys")
        elif args.what == "buttons":
            _print_rows(conf.custom_buttons(),
                        ["index", "label", "description", "event_type", "value", "action", "command", "visible"])
        elif args.what == "default-buttons":
            _print_rows(conf.default_buttons(), ["button", "action", "command", "visible"])
        elif args.what == "sliders":
            _print_rows(conf.sliders(), ["slider", "event_type", "action", "visible"])
        elif args.what == "alarms":
            _print_rows(conf.alarms(), ["index", "enabled", "from", "offset_s", "source", "cond",
                                        "temperature", "action", "string", "guard", "negguard", "beep"])
    elif args.cmd == "get":
        raw = conf.get(args.section, args.key)
        if raw is None:
            print(f"{args.section}/{args.key}: not set (Artisan default)")
            return 1
        print(raw)
    elif args.cmd == "set":
        before = conf.get(args.section, args.key)
        if len(args.values) == 1:
            conf.set_list(args.section, args.key, args.values[0])
        else:
            conf.set_list(args.section, args.key, args.values)
        conf.save(args.file)
        print(f"{args.section}/{args.key}: {before!r} -> {conf.get(args.section, args.key)!r}")
    elif args.cmd == "check":
        bad = conf.roundtrip_mismatches()
        for s, k in bad:
            print(f"  would change: [{s}] {k}")
        print("round trip clean" if not bad else f"{len(bad)} key(s) would change")
        return 1 if bad else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
