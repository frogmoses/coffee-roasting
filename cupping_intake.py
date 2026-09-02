"""Structured cupping intake: a few perceptual questions instead of a wheel.

Naming flavors ("bergamot", "stone fruit") is a vocabulary skill that takes
years and mostly applies to light roasts of aromatic origins. Steering a
roast does not need it. It needs a handful of axes almost anyone can
perceive, plus a preference. Each axis maps to a roast mechanic:

- sour/bright vs bitter/roasty  -> under- vs over-development (the FC->DROP
  seconds lever). This is the primary steering axis.
- astringency (drying, "aspirin") -> underdeveloped interior / too-fast
  drying / scorched surface — the roaster's own history shows this cleared
  up when the first heater cut moved earlier.
- sweetness -> development and Maillard time landing well.
- body -> development / roast level (thin = under, heavy = darker).
- preference vs the previous batch -> the signal the dial-in actually runs on.

Answers are stored as a dict in history (`cupping_intake`) and also rendered
to a sentence (`intake_to_text`) that is written into `cupping_notes`, so the
existing LLM flavor loop consumes it without any schema changes.
"""

# Each question: key, the prompt shown to the roaster, and the allowed
# answers as (value, label) pairs. Values are small ints or short strings so
# they are easy to type and unambiguous in the prompt.
QUESTIONS = [
    {
        "key": "brew",
        "prompt": "How did you brew it?",
        "choices": [
            ("switch", "Hario Switch"),
            ("espresso", "espresso"),
            ("pourover", "pour-over / drip"),
            ("cupping", "cupping bowl / immersion"),
            ("other", "other"),
        ],
    },
    {
        "key": "rest_days",
        "prompt": "Days since roast?",
        "choices": None,  # free integer
    },
    {
        "key": "balance",
        "prompt": "Sour/bright ... or bitter/roasty? (the main steering axis)",
        "choices": [
            (-2, "very sour, grassy, or sharp"),
            (-1, "a little sour or bright"),
            (0, "balanced, neither stands out"),
            (1, "a little roasty or bitter"),
            (2, "very roasty, ashy, or burnt"),
        ],
    },
    {
        "key": "astringency",
        "prompt": "Astringent / drying / aspirin-like?",
        "choices": [
            (0, "clean, none"),
            (1, "slight drying at the finish"),
            (2, "noticeable"),
            (3, "harsh, aspirin-like"),
        ],
    },
    {
        "key": "sweetness",
        "prompt": "Sweetness?",
        "choices": [
            (0, "none"),
            (1, "a hint"),
            (2, "clearly sweet"),
            (3, "rich, syrupy"),
        ],
    },
    {
        "key": "body",
        "prompt": "Body / mouthfeel?",
        "choices": [
            (-1, "thin, watery"),
            (0, "medium"),
            (1, "heavy, creamy"),
        ],
    },
    {
        "key": "preference",
        "prompt": "Compared with the previous batch of this bean?",
        "choices": [
            ("worse", "worse"),
            ("same", "about the same"),
            ("better", "better"),
            ("na", "no basis to compare"),
        ],
    },
    {
        "key": "drink_again",
        "prompt": "Would you happily drink this again?",
        "choices": [
            ("yes", "yes"),
            ("meh", "it's fine"),
            ("no", "no"),
        ],
    },
    {
        "key": "notes",
        "prompt": "Anything else you noticed? (free text, optional)",
        "choices": None,  # free text
    },
]

# Fixed order for rendering and for the LLM legend
_LABELS = {q["key"]: dict(q["choices"]) for q in QUESTIONS if q["choices"]}


def _coerce(key, raw):
    """Turn a typed/JSON answer into the stored value, or raise ValueError.

    Accepts the choice value itself, its 1-based menu number, or (for string
    choices) a case-insensitive label prefix. Blank -> None (skipped).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return None
    question = next(q for q in QUESTIONS if q["key"] == key)
    choices = question["choices"]
    if choices is None:
        if key == "rest_days":
            try:
                return int(raw)
            except (TypeError, ValueError):
                raise ValueError(f"{key}: expected a whole number of days, got {raw!r}")
        return str(raw)
    values = [v for v, _ in choices]
    # Exact value (int or str) — JSON input path
    if raw in values:
        return raw
    text = str(raw).strip().lower()
    # Menu number
    if text.isdigit() and 1 <= int(text) <= len(choices):
        return values[int(text) - 1]
    # Integer scale typed as text ("-1", "2")
    try:
        as_int = int(text)
        if as_int in values:
            return as_int
    except ValueError:
        pass
    # Label / string value prefix match
    for value, label in choices:
        if text == str(value).lower() or label.lower().startswith(text):
            return value
    raise ValueError(f"{key}: {raw!r} is not one of {values}")


def normalize_intake(answers):
    """Validate a dict of raw answers (from JSON or prompts) into stored form.

    Unknown keys are dropped; missing or blank answers are omitted. Raises
    ValueError on an answer that doesn't fit its question.
    """
    out = {}
    for q in QUESTIONS:
        key = q["key"]
        if key not in answers:
            continue
        value = _coerce(key, answers[key])
        if value is not None:
            out[key] = value
    if not out:
        raise ValueError("no intake answers given")
    return out


def run_intake(ask=input, say=print):
    """Interactive intake: prompt each question on the terminal.

    Blank answers skip a question. Re-prompts on an invalid answer.

    Args:
        ask: input-like callable (injected for tests).
        say: print-like callable.

    Returns:
        Normalized intake dict (see normalize_intake).
    """
    say("Cupping intake — answer with the number, or press Enter to skip.")
    answers = {}
    for q in QUESTIONS:
        say("")
        say(q["prompt"])
        if q["choices"]:
            for i, (_, label) in enumerate(q["choices"], 1):
                say(f"  {i}. {label}")
        while True:
            raw = ask("> ")
            try:
                value = _coerce(q["key"], raw)
                break
            except ValueError as e:
                say(f"  {e}")
        if value is not None:
            answers[q["key"]] = value
    return normalize_intake(answers) if answers else {}


def intake_to_text(intake):
    """Render an intake dict as one plain-English paragraph.

    This is what gets stored in `cupping_notes` (so the LLM flavor loop and
    the prior-roast history see it) and shown by `cupping <id>`.
    """
    if not intake:
        return ""
    parts = []
    brew = intake.get("brew")
    rest = intake.get("rest_days")
    if brew or rest is not None:
        how = f"Brewed as {_LABELS['brew'].get(brew, brew)}" if brew else "Brewed"
        if rest is not None:
            how += f" {rest} day{'s' if rest != 1 else ''} after roast"
        parts.append(how + ".")
    for key, prefix in (
        ("balance", "Balance"),
        ("astringency", "Astringency"),
        ("sweetness", "Sweetness"),
        ("body", "Body"),
    ):
        if key in intake:
            parts.append(f"{prefix}: {_LABELS[key][intake[key]]}.")
    if "preference" in intake:
        parts.append(
            "Versus previous batch: "
            f"{_LABELS['preference'][intake['preference']]}."
        )
    if "drink_again" in intake:
        parts.append(f"Drink again: {_LABELS['drink_again'][intake['drink_again']]}.")
    if intake.get("notes"):
        parts.append(f"Notes: {intake['notes']}")
    return " ".join(parts)


# Legend handed to the LLM so it reads the scores the same way every time.
INTAKE_LEGEND = """\
The roaster's cupping notes may come from a STRUCTURED INTAKE rather than \
free-form flavor vocabulary — the roaster is not a trained cupper and has \
said so. Read the axes as roast mechanics, not as flavor-wheel descriptors:
- Balance: sour/bright/grassy = under-developed (too few seconds after FC, \
or too little energy into FC); roasty/bitter/ashy = over-developed or too \
dark a drop. "Balanced" is the goal for this bean.
- Astringency (drying, aspirin): uneven or under-developed interior, or a \
scorched surface from too much heat too early. This roaster's own history \
shows it cleared when the first heater cut moved earlier.
- Sweetness and body: rise with adequate Maillard and development time; \
"none" and "thin" point the same way as "sour".
- "Versus previous batch" and "drink again" are the preference signal. \
Weight them above any descriptor: the goal is a roast the roaster prefers, \
and the roast-to-roast delta that produced "better" is the thing to keep.
"""
