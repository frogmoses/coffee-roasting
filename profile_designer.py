"""Profile designer: the model writes the roast schedule, the validator bounds it.

The inversion of the old workflow. Instead of the operator authoring a
schedule that the analyzer critiques afterwards, the designer takes the bean
dossier (what the coffee is and how the supplier says to roast it), the
machine envelope (what this Hottop has measurably done: the executed
staircase, the median curve, first-crack statistics, heat-soak, ambient,
ear latency) and the operator's preferences, and returns a schedule plus
its predictions and a flavor hypothesis. `roast_schedule.validate_schedule`
then clamps or refuses it; one repair round is offered; a design that still
fails is dropped and the caller falls back to the measured staircase, so a
roast day never depends on the model.

Two entry points:
- `design_profile`: a first version (or a fresh design) against a baseline
  (the bean's measured staircase, or a borrowed one for a new bean).
- `revise_profile`: the next version from the cupping verdicts of the roasts
  that ran on the previous one, bounded to a few small changes so the cup
  can be attributed to them. An empty change list means "roast it again".

Every number in the prompt is labelled with where it came from; the model is
told the rules it has to design inside, verbatim from `roast_schedule`.
"""

import json

from llm_client import structured_call
from hottop_reference import HOTTOP_CONTROLS, HOTTOP_DYNAMICS, OPERATOR_PREFS
from cupping_intake import INTAKE_LEGEND
from bean_dossier import dossier_block
from roast_schedule import (
    validate_schedule, schedule_from_envelope, staircase_summary, new_schedule,
    RULES_TEXT, DEV_CAP_S,
)
from machine_envelope import compact_envelope

# A first design may differ from the measured staircase in more places than a
# revision may differ from its baseline.
FIRST_DESIGN_MAX_CHANGES = 6
REVISION_MAX_CHANGES = 3

_TRIGGER = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["bt", "time"]},
        "bt": {"type": "number"},
        "after": {"type": "string", "enum": ["CHARGE", "DRY", "FCs"]},
        "seconds": {"type": "number"},
    },
    "required": ["kind", "bt", "after", "seconds"],
    "additionalProperties": False,
}
_MOVE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "trigger": _TRIGGER,
        "action": {"type": "string", "enum": ["heater", "fan"]},
        "value": {"type": "integer"},
        "why": {"type": "string"},
    },
    "required": ["id", "trigger", "action", "value", "why"],
    "additionalProperties": False,
}
_DESIGN_SCHEMA = {
    "type": "object",
    "properties": {
        "schedule": {
            "type": "object",
            "properties": {
                "charge": {
                    "type": "object",
                    "properties": {"bt": {"type": "number"}, "heater": {"type": "integer"},
                                   "fan": {"type": "integer"}, "why": {"type": "string"}},
                    "required": ["bt", "heater", "fan", "why"],
                    "additionalProperties": False,
                },
                "moves": {"type": "array", "items": _MOVE},
                "post_fc": {"type": "array", "items": _MOVE},
                "drop_seconds": {"type": "integer"},
            },
            "required": ["charge", "moves", "post_fc", "drop_seconds"],
            "additionalProperties": False,
        },
        "predicted": {
            "type": "object",
            "properties": {"fc_time_s": {"type": "integer"}, "fc_bt": {"type": "number"},
                           "drop_bt": {"type": "number"}, "total_time_s": {"type": "integer"},
                           "confidence": {"type": "string", "enum": ["measured", "borrowed", "guess"]}},
            "required": ["fc_time_s", "fc_bt", "drop_bt", "total_time_s", "confidence"],
            "additionalProperties": False,
        },
        "roast_level_target": {"type": "string"},
        "flavor_hypothesis": {"type": "string"},
        "contrast_lever": {
            "type": "object",
            "properties": {"key": {"type": "string", "enum": ["dev_seconds", "fan_after_fc", "first_cut_bt"]},
                           "values": {"type": "array", "items": {"type": "number"}},
                           "why": {"type": "string"}},
            "required": ["key", "values", "why"],
            "additionalProperties": False,
        },
        "watch_for": {"type": "array", "items": {"type": "string"}},
        "changes_from_baseline": {
            "type": "array",
            "items": {"type": "object",
                      "properties": {"what": {"type": "string"}, "from": {"type": "string"},
                                     "to": {"type": "string"}, "why": {"type": "string"}},
                      "required": ["what", "from", "to", "why"],
                      "additionalProperties": False},
        },
        "rationale": {"type": "string"},
    },
    "required": ["schedule", "predicted", "roast_level_target", "flavor_hypothesis",
                 "contrast_lever", "watch_for", "changes_from_baseline", "rationale"],
    "additionalProperties": False,
}

_DESIGN_SYSTEM_PROMPT = (
    """\
You are the roasting expert for a home roaster running a Hottop KN-8828B-2K+ \
under Artisan. You design the roast; the operator loads beans, charges, and \
tastes. Your schedule is executed by Artisan alarms exactly as written, so \
every move must be one this machine can make and the whole plan must be safe.

Design from three things: what the bean is and how its supplier says to roast \
it (the dossier), what this machine has measurably done (the envelope: an \
executed heater staircase with the median time and bean temperature of each \
level, a median BT curve, first-crack statistics, heat-soak and ambient \
effects), and what the operator will drink. Every number you write is a \
prediction from the envelope, and you label it: "measured" when this bean's \
own roasts back it, "borrowed" when the envelope came from another bean on \
the same machine, "guess" otherwise.

How the levers work here: the charge state and 20-30% heater moves made 1-2 \
minutes ahead are the steering; a lone 10% step is a trim; the fan is airflow \
early (chaff, convection) and a development brake late; development is \
seconds from first crack, and the drop temperature is an outcome of that. \
Prefer few, deliberate moves to many small ones. A lighter target (City, \
City+) means a shorter development and no post-FC heat; a Full City target \
means longer development, still under the cap. Naturals and low-density \
coffees want gentler early heat; dense high-grown washed coffees take more.

""" + RULES_TEXT + """

Output: the schedule in the given JSON shape (a trigger is either kind "bt" \
with the temperature, or kind "time" with the From event and seconds; fill \
the unused fields with 0 or "CHARGE"), the predictions, the roast level you \
are aiming at, a flavor hypothesis (what the cup should show if the design \
is right, in the intake's terms: balance, astringency, sweetness, body), a \
contrast lever for the first session (three values of ONE lever to roast as a \
bracket, the design's own value in the middle; dev_seconds unless the cup \
history says development is settled), two to four things to watch for, the \
list of changes from the baseline with a reason each, and a short rationale. \
When revising from cupping verdicts, change as little as the cup demands and \
return an empty change list when the right move is to roast the same schedule \
again.

""" + HOTTOP_CONTROLS + "\n" + HOTTOP_DYNAMICS + "\n" + OPERATOR_PREFS + "\n" + INTAKE_LEGEND
)


# ------------------------------------------------------------ prompt blocks

def _envelope_block(envelope):
    """The machine envelope as labelled JSON."""
    env = compact_envelope(envelope)
    note = ""
    if env.get("borrowed_from"):
        note = (f"NOTE: this bean has no roasts here; the envelope is borrowed from "
                f"{env['borrowed_from']} on the same machine (same charge and warm-up), "
                f"so the staircase transfers but first crack is uncertain by about a minute. "
                f"dev_history and its cupping verdicts are {env['borrowed_from']}'s, not this "
                f"bean's: use them as a prior from a different coffee, weighed by how alike "
                f"the two beans are (process, variety, density), never as this bean's results.\n")
    note += ("dev_history: dev_s runs from the operator's FCs mark; dev_s_corrected runs "
             "from the true first crack (ear or curve) where the mark was off by more than "
             "30 s (mark_suspect). Reason with dev_s_corrected.\n")
    keys = ("count", "source_roasts", "staircase", "charge_bt", "tp_bt", "tp_time", "dry_time",
            "fc_stats", "ror_at_fc", "heat_soak_shift", "ambient", "dev_history",
            "ear_latency_s", "ear_latency_n", "median_curve")
    slim = {k: env.get(k) for k in keys if env.get(k) not in (None, [], {})}
    return note + json.dumps(slim, indent=1, default=str)


def _versions_block(prior_versions):
    """Earlier profile versions with how they ran and cupped."""
    if not prior_versions:
        return ""
    lines = []
    for p in prior_versions:
        lines.append(f"v{p.get('version')} [{p.get('status')}]: {staircase_summary(p.get('schedule', {}))}")
        pred = p.get("predicted") or {}
        if pred:
            lines.append(f"  predicted FC {pred.get('fc_time_s')}s @ {pred.get('fc_bt')}F, drop {pred.get('drop_bt')}F")
        if p.get("flavor_hypothesis"):
            lines.append(f"  hypothesis: {p['flavor_hypothesis']}")
        for c in p.get("changes_from_baseline") or []:
            lines.append(f"  changed: {c.get('what')} {c.get('from')} -> {c.get('to')}")
        for r in p.get("roasts") or []:
            bits = [f"batch {r.get('batch_nr')} {r.get('roast_date')}"]
            if r.get("dev_s"):
                bits.append(f"dev {r['dev_s']:.0f}s")
            if r.get("fc_time"):
                bits.append(f"FC {r['fc_time']:.0f}s @ {r.get('fc_bt')}F")
            if r.get("drop_bt"):
                bits.append(f"drop {r['drop_bt']}F")
            if r.get("execution"):
                bits.append(f"execution: {r['execution']}")
            intake = r.get("cupping_intake") or {}
            if intake:
                bits.append("cup: " + ", ".join(f"{k} {v}" for k, v in intake.items() if v not in (None, "")))
            elif r.get("cupping_notes"):
                bits.append(f"cup: {r['cupping_notes'][:160]}")
            lines.append("  " + "; ".join(bits))
    return "\n".join(lines)


def _build_design_content(dossier, envelope, baseline, prior_versions=None, verdicts=None,
                          task_text="", errors=None, bad_design=None):
    sections = ["BEAN DOSSIER:", dossier_block(dossier), "",
                "MACHINE ENVELOPE (this Hottop, from the roast history):", _envelope_block(envelope), "",
                "BASELINE SCHEDULE (what the machine has run; express your design as changes from it):",
                staircase_summary(baseline), json.dumps({k: baseline.get(k) for k in ("charge", "moves", "post_fc", "drop")},
                                                        default=str)]
    versions = _versions_block(prior_versions)
    if versions:
        sections += ["", "PROFILE VERSIONS SO FAR (oldest first):", versions]
    if verdicts:
        sections += ["", "CUPPING VERDICTS DRIVING THIS REVISION:"]
        for v in verdicts:
            sections.append("- " + json.dumps(v, default=str))
    if errors:
        sections += ["", "YOUR PREVIOUS DESIGN WAS REJECTED BY THE VALIDATOR:"]
        sections += [f"- {e}" for e in errors]
        sections += ["It was:", json.dumps(bad_design, default=str), "Return a corrected design."]
    sections += ["", task_text or "Design the roast profile."]
    return "\n".join(sections)


# ------------------------------------------------------------ conversion

def _to_schedule(design, baseline):
    """The model's JSON shape -> the internal schedule shape."""
    s = design.get("schedule") or {}
    charge = dict(s.get("charge") or {})
    charge.setdefault("warmup", (baseline.get("charge") or {}).get("warmup"))
    if not charge.get("why"):
        charge.pop("why", None)

    def conv(mv):
        trig = mv.get("trigger") or {}
        if trig.get("kind") == "bt":
            trigger = {"bt": trig.get("bt")}
        else:
            trigger = {"after": trig.get("after") or "CHARGE", "seconds": trig.get("seconds") or 0}
        return {"id": mv.get("id"), "trigger": trigger, "action": mv.get("action"),
                "value": mv.get("value"), "why": mv.get("why", "")}

    sched = new_schedule(charge=charge, moves=[conv(m) for m in s.get("moves", [])],
                         post_fc=[conv(m) for m in s.get("post_fc", [])],
                         drop_s=s.get("drop_seconds") or 150,
                         guards=baseline.get("guards"),
                         predicted=design.get("predicted"))
    return sched


# ------------------------------------------------------------ entry points

def _run(dossier, envelope, baseline, prefs, prior_versions, verdicts, task_text,
         max_changes, call):
    """One design call with a repair round; returns (result, status)."""
    content = _build_design_content(dossier, envelope, baseline, prior_versions, verdicts, task_text)
    design, status = call(_DESIGN_SYSTEM_PROMPT, content, _DESIGN_SCHEMA, task="to design this profile")
    if not design:
        return None, status
    repairs = 0
    for _ in range(2):
        sched = _to_schedule(design, baseline)
        res = validate_schedule(sched, envelope, prefs, baseline=baseline, max_changes=max_changes)
        if res["ok"]:
            return {
                "schedule": res["schedule"],
                "predicted": design.get("predicted") or {},
                "roast_level_target": design.get("roast_level_target", ""),
                "flavor_hypothesis": design.get("flavor_hypothesis", ""),
                "contrast_lever": design.get("contrast_lever"),
                "watch_for": design.get("watch_for") or [],
                "changes_from_baseline": design.get("changes_from_baseline") or [],
                "validator_changes": res["schedule"].get("changes_from_baseline", []),
                "rationale": design.get("rationale", ""),
                "validator": {"errors": [], "warnings": res["warnings"], "clamped": res["clamped"]},
                "repair_rounds": repairs,
            }, "ok"
        if repairs:
            return None, "design invalid after repair: " + "; ".join(res["errors"])
        repairs += 1
        content = _build_design_content(dossier, envelope, baseline, prior_versions, verdicts,
                                        task_text, errors=res["errors"], bad_design=design)
        design, status = call(_DESIGN_SYSTEM_PROMPT, content, _DESIGN_SCHEMA, task="to repair this profile")
        if not design:
            return None, status
    return None, "design invalid"


def design_profile(dossier, envelope, prefs=None, prior_versions=None, baseline=None,
                   dev_s=None, call=structured_call):
    """A first design (or a fresh one) for the bean. Returns (result, status).

    `baseline` defaults to the envelope's measured staircase with `dev_s`
    (default: the last cupped-best development, else 150 s).
    """
    prefs = dict(prefs or {})
    prefs.setdefault("dev_cap_s", DEV_CAP_S)
    if baseline is None:
        baseline = schedule_from_envelope(envelope, dev_s=dev_s or _best_dev(envelope) or 150)
    target = (dossier or {}).get("target_level") or "City+"
    task = (f"Design version {1 + len(prior_versions or [])} of this bean's profile, aiming at "
            f"{target}. Use the baseline's proven moves where they serve the bean and change "
            f"only what the dossier or the cup history argues for.")
    return _run(dossier, envelope, baseline, prefs, prior_versions, None, task,
                FIRST_DESIGN_MAX_CHANGES, call)


def revise_profile(profile, dossier, envelope, verdicts, prefs=None, prior_versions=None,
                   reference=None, call=structured_call):
    """The next version from cupping verdicts. Baseline = the reference
    schedule when one exists, else the profile being revised."""
    prefs = dict(prefs or {})
    prefs.setdefault("dev_cap_s", DEV_CAP_S)
    baseline = (reference or profile).get("schedule")
    which = f"the reference (v{reference['version']})" if reference else f"v{profile.get('version')}"
    task = (f"Revise from {which} given how its roasts cupped. Change at most "
            f"{REVISION_MAX_CHANGES} things, each inside the bounds, and only what the cup "
            f"demands; return an empty change list if the schedule should be roasted again as is.")
    return _run(dossier, envelope, baseline, prefs, prior_versions, verdicts, task,
                REVISION_MAX_CHANGES, call)


def _best_dev(envelope):
    """Development seconds of the best-cupped roast in the envelope, if any."""
    best, best_score = None, None
    for r in (envelope or {}).get("dev_history") or []:
        dev = r.get("dev_s_corrected") or r.get("dev_s")
        if not dev or not r.get("cupped"):
            continue
        score = {"yes": 2, "meh": 1, "no": 0}.get(r.get("drink_again"), 0)
        score += {"better": 1, "same": 0, "worse": -1}.get(r.get("preference"), 0)
        score -= abs(r.get("balance") or 0) * 0.5
        if best_score is None or score > best_score:
            best, best_score = dev, score
    return int(round(best)) if best else None
