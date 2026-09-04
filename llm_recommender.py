"""LLM-backed recommendation engine for coffee roast analysis.

Hands Claude the full picture of a roast — extracted metrics, RoR diagnostics,
the move-by-move control timeline (heater/fan changes the operator actually
made), the bean's intended flavor profile, the roaster's own cupping notes,
and visual development data — plus a reference for the specific machine, and
asks for advice tied to concrete dial moves.

There are NO numeric target bands. The roaster hasn't dialed in this bean yet,
so any fixed "target curve" would be an unvalidated guess. Instead the model
reasons from (1) the bean's intended flavor vs how it actually cupped and
(2) bean-agnostic roasting theory (RoR shape, phase balance, crash/flick,
deceleration). Targets become meaningful only once the roaster has a roast
they love — then that roast's curve is the reference, not a theory band.

The call runs once per roast at scan time and the result is cached in
roast_history.json. If the API key or network is unavailable, this returns
(None, status) and the scan still saves the metrics — just no recommendations.

Security: the Anthropic client reads ANTHROPIC_API_KEY from the environment
(injected by the run_roast-analyzer wrapper). No .env loading, no key in code.
"""

import json

from roast_metrics import _fmt_time, build_curve_series
from roast_narrative import build_control_timeline, format_narrative
from hottop_reference import HOTTOP_CONTROLS
from cupping_intake import INTAKE_LEGEND

# Opus 4.8 — best reasoning, closest to a direct expert read of the log.
# Runs a few times a week (once per new roast), cached after.
MODEL = "claude-opus-4-8"

# Structured-output schema: the same rec shape the display layer already
# expects ({priority, category, text, full_text?}) plus next_roast actions,
# so nothing downstream changes.
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "priority": {"type": "integer", "enum": [1, 2, 3]},
                    "category": {"type": "string"},
                    "text": {"type": "string"},
                    "full_text": {"type": "string"},
                },
                "required": ["priority", "category", "text"],
                "additionalProperties": False,
            },
        },
        "next_roast": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["recommendations", "next_roast"],
    "additionalProperties": False,
}

# Built by concatenation (not %-formatting) because the prompt and the Hottop
# reference both contain literal "%" signs (heater %, fan %).
_SYSTEM_PROMPT = (
    """\
You are an expert coffee-roasting coach for a home roaster running a Hottop \
KN-8828B-2K+ in manual mode with Artisan. You are reviewing one roast.

IMPORTANT: there are no fixed numeric targets here. The roaster has not yet \
dialed in this bean, so any "target curve" with specific phase percentages, \
times, or drop temperatures would be an unvalidated guess — do NOT judge the \
roast against invented numeric bands, and do not present any number as a \
target the roast should have hit. Judge it two ways instead:

1. FLAVOR: compare the bean's intended flavor profile against how it actually \
cupped (the roaster's own cupping notes, when present). If it cupped flat, \
ashy, grassy, baked, sour, etc., work backward to the roast mechanics that \
cause that fault. If there are no cupping notes yet, say what to taste for.

2. THEORY (bean-agnostic, reliable regardless of target): a smoothly declining \
rate of rise; an ever-decelerating bean temp through Maillard (Rao's rule — a \
rising RoR means heat went in too late); no crash or flick into/after first \
crack; a sensible balance between drying, Maillard, and development; and the \
fact that drop temp and weight loss are OUTCOMES of development time, not dials.

Tie every recommendation to the actual control moves the roaster made (you are \
given the heater/fan timeline) and to this machine's levers — heater %, fan %, \
and the timing of cuts relative to bean temperature (BT) and first crack (FC). \
Name the dial and the moment ("ease the heater to ~80% by 250F BT", "make one \
cut around 340F and hold it through FC"). Do not say "charge hotter" if the \
timeline shows the heater was already maxed — use the airflow and timing levers.

You may reference a metric's value as a fact ("development ran 2:30, ~20% of \
the roast") and reason about whether that serves the bean's flavor — but frame \
it as roasting judgment, not conformance to a number.

You are given a downsampled BT/RoR series for the roast. The RoR diagnostic \
flags (crash/flick/rising) are machine-tuned heuristics — check them against \
the curve itself, and trust the curve when they disagree.

fc_mark_check (when present) locates first crack from the BT curve itself — \
the steam-release RoR crash — and gives its offset from the operator's FCs \
mark. This bean cracks quietly and the operator marks by ear, so a large \
offset (mark_suspect) means the development time and everything timed from \
FC are uncertain by about that much; say so, and prefer advice that doesn't \
hinge on a precise FC. A consistent offset across previous roasts is worth \
naming: it says the operator marks systematically early or late.

fc_audio_check (when present) is a microphone crack detector's first-crack \
onset, with its offset from the operator's mark. When audio and the curve \
crash agree within ~15s, treat that as the true FC and the operator's mark as \
off by the offset; when they disagree, trust audio for the onset and the curve \
for the thermal effect, and say FC timing is uncertain. Prefer audio over the \
by-ear mark on this quiet bean. "Not declared" with few cracks after arming \
means the microphone missed it (placement/gain), not that FC never happened.

If DATA QUALITY WARNINGS are listed, treat the affected metrics as unreliable: \
skip or hedge advice that depends on them, and if the problem blocks analysis \
(e.g. CHARGE or FC never marked), make fixing the recording the top action.

If PREVIOUS ROASTS of this bean are shown, use them: note whether the roaster \
applied the earlier next-roast advice, whether the change moved the roast in \
the intended direction (especially in the cup), and build on that history \
rather than repeating generic advice. The roast-to-roast deltas are the real \
dial-in signal.

"""
    + HOTTOP_CONTROLS
    + "\n"
    + INTAKE_LEGEND
    + """
Output rules:
- recommendations: ordered most important first. priority 1 = fix this first, \
2 = worth improving, 3 = informational. category is a short label like \
"Heat Control", "Phase Timing", "RoR Control", "Temperature", "Bean Profile", \
"Flavor Goal", "Visual Dev". text is 1-3 sentences. For flavor advice that \
references long professional cupping notes, put a 2-sentence version in text and \
the full version in full_text.
- next_roast: 2-4 short imperative action items for the next roast, each a \
single concrete change. Deduplicate — don't repeat the same fix two ways.
- If nothing meaningful stands out (the curve looks clean and there's no flavor \
fault to chase), return few or no recommendations rather than inventing problems.
"""
)


def _curated_metrics(metrics):
    """Pull the metrics worth handing to the model into a compact dict.

    Avoids dumping the whole metrics dict (visual trajectories, etc.) so the
    prompt stays focused on the numbers that drive roast advice.
    """
    keys = [
        "charge_bt", "charge_et", "tp_bt", "tp_time",
        "dry_phase_pct", "mid_phase_pct", "dev_phase_pct",
        "dry_phase_time", "mid_phase_time", "dev_phase_time", "total_time",
        "fc_bt", "fc_time", "drop_bt", "drop_time",
        "ror_at_fc", "dry_phase_ror", "mid_phase_ror", "dev_phase_ror",
        "heat_adjustments", "weight_in", "weight_out", "weight_loss_pct",
    ]
    out = {k: metrics.get(k) for k in keys if metrics.get(k) not in (None, 0)}
    # heat_adjustments is the one metric where 0 is a meaningful fact (zero
    # heater moves the whole roast), not "unrecorded" — keep it explicitly.
    if "heat_adjustments" in metrics:
        out["heat_adjustments"] = metrics["heat_adjustments"]
    fc_check = metrics.get("fc_check")
    if fc_check:
        out["fc_mark_check"] = {k: fc_check.get(k) for k in (
            "detected_time", "detected_bt", "offset", "mark_suspect", "details",
        ) if fc_check.get(k) is not None}
    fc_audio = metrics.get("fc_audio")
    if fc_audio:
        out["fc_audio_check"] = {k: fc_audio.get(k) for k in (
            "detected_time", "detected_bt", "offset", "mark_suspect",
            "crack_count", "cracks_after_arm", "peak_cpm", "details",
        ) if fc_audio.get(k) is not None}
    ror = metrics.get("ror_smoothness", {})
    if ror:
        out["ror_diagnostics"] = {
            k: ror.get(k) for k in (
                "severity", "oscillations", "heat_correlation",
                "fc_crash", "fc_flick", "crash_min_ror",
                "ror_rising", "ror_rise", "details",
            ) if ror.get(k) is not None
        }
    return out


def _bean_block(bean_profile):
    """Render bean profile context, if present."""
    if not bean_profile:
        return "No bean profile available."
    parts = []
    name = bean_profile.get("name")
    if name:
        parts.append(f"Bean: {name}")
    notes = bean_profile.get("cupping_notes")
    if notes:
        parts.append(f"Professional cupping notes: {notes}")
    dominant = bean_profile.get("dominant_flavors")
    if dominant:
        flav = ", ".join(f"{n} ({s})" for n, s in dominant)
        parts.append(f"Dominant flavors: {flav}")
    cupping = bean_profile.get("cupping_scores")
    if cupping:
        parts.append("Cupping scores: " + json.dumps(cupping))
    return "\n".join(parts) if parts else "No bean profile available."


def _visual_block(metrics):
    """Render sentinel visual development context, if present."""
    traj = metrics.get("visual_development_scores")
    if not traj:
        return ""
    final = metrics.get("visual_final_score")
    uniformity = metrics.get("visual_uniformity", "unknown")
    color = metrics.get("visual_final_color", "")
    points = "; ".join(
        f"{_fmt_time(p.get('elapsed', 0))} score {p.get('score')}"
        + (f" BT {p['bt']}F" if p.get("bt") else "")
        for p in traj
    )
    return (
        f"Visual development (camera): final score {final}/10, "
        f"uniformity {uniformity}, final color \"{color}\".\n"
        f"Trajectory: {points}"
    )


def _curve_block(data):
    """Render the downsampled BT/RoR series as prompt text lines."""
    rows = build_curve_series(data)
    if not rows:
        return ""
    lines = []
    for r in rows:
        ror = f"RoR {r['ror']:5.1f}" if r["ror"] is not None else "RoR    --"
        marker = f"   [{r['marker']}]" if r["marker"] else ""
        lines.append(f"{_fmt_time(r['time']):>5}  BT {r['bt']:6.1f}F  {ror}{marker}")
    return "\n".join(lines)


def _observations_block(data):
    """Operator-marked physical flags and roasting notes from Artisan."""
    parts = []
    flags = [label for key, label in (
        ("heavy_fc", "heavy first crack"),
        ("low_fc", "quiet/low first crack"),
        ("oily", "oily surface"),
        ("tipping", "tipping"),
        ("scorching", "scorching"),
    ) if data.get(key)]
    if flags:
        parts.append("Operator-marked observations: " + ", ".join(flags))
    notes = data.get("roasting_notes", "")
    if notes:
        parts.append(f"Roasting notes (operator's intent/plan for this roast): {notes}")
    return "\n".join(parts)


def _prior_block(prior_roasts):
    """Render earlier roasts of the same bean: facts, prior advice, and how
    each cupped — the dial-in history the model should build on."""
    if not prior_roasts:
        return ""
    lines = []
    for p in prior_roasts:
        m = p.get("metrics", {})
        facts = []
        if m.get("total_time"):
            facts.append(f"total {_fmt_time(m['total_time'])}")
        if m.get("fc_time"):
            fc = f"FC {_fmt_time(m['fc_time'])}"
            if m.get("fc_bt"):
                fc += f" @ {m['fc_bt']:g}F"
            facts.append(fc)
        if m.get("dev_phase_time"):
            dev = f"dev {_fmt_time(m['dev_phase_time'])}"
            if m.get("dev_phase_pct"):
                dev += f" ({m['dev_phase_pct']:g}%)"
            facts.append(dev)
        if m.get("drop_bt"):
            facts.append(f"drop {m['drop_bt']:g}F")
        if m.get("weight_loss_pct"):
            facts.append(f"{m['weight_loss_pct']:g}% loss")
        if m.get("heat_adjustments") is not None:
            facts.append(f"{m['heat_adjustments']} heater moves")
        ror_bits = [b for b, flag in (
            (p.get("ror_severity", ""), True),
            ("FC crash", p.get("fc_crash")),
            ("FC flick", p.get("fc_flick")),
            ("rising Maillard RoR", p.get("ror_rising")),
        ) if flag and b]
        if ror_bits:
            facts.append("RoR " + ", ".join(ror_bits))
        if p.get("fc_offset") is not None:
            facts.append(f"curve FC {p['fc_offset']:+d}s vs mark")
        if p.get("fc_audio_offset") is not None:
            facts.append(f"audio FC {p['fc_audio_offset']:+d}s vs mark")
        header = f"Batch {p.get('batch_nr', '?')} ({p.get('roast_date', 'unknown date')})"
        lines.append(f"{header}: {', '.join(facts) if facts else 'no metrics recorded'}")
        advice = p.get("next_roast") or []
        if advice:
            lines.append(f"  Advised after that roast: {'; '.join(advice)}")
        notes = p.get("cupping_notes", "")
        lines.append(f"  Cupped: \"{notes}\"" if notes else "  Cupped: (no notes)")
    return "\n".join(lines)


def _build_user_content(metrics, data, bean_profile, narrative_text,
                        cupping_notes="", warnings=None, prior_roasts=None):
    """Assemble the full analysis prompt body."""
    sections = []
    if warnings:
        sections += [
            "DATA QUALITY WARNINGS (recording problems — hedge advice that depends on these):",
            "\n".join(f"- {w}" for w in warnings),
            "",
        ]
    sections += [
        "KEY METRICS (facts of this roast — not compared to any target band):",
        json.dumps(_curated_metrics(metrics), indent=2, default=str),
    ]
    curve = _curve_block(data)
    if curve:
        sections += [
            "",
            "BT/RoR CURVE (downsampled ~30s, BT lightly smoothed, RoR in F/min):",
            curve,
        ]
    sections += [
        "",
        "CONTROL TIMELINE (the moves the roaster actually made, CHARGE->DROP):",
        narrative_text,
        "",
        "BEAN PROFILE (what this bean is supposed to taste like):",
        _bean_block(bean_profile),
    ]
    observations = _observations_block(data)
    if observations:
        sections += ["", "OPERATOR OBSERVATIONS:", observations]
    visual = _visual_block(metrics)
    if visual:
        sections += ["", "VISUAL DEVELOPMENT:", visual]
    prior = _prior_block(prior_roasts)
    if prior:
        sections += ["", "PREVIOUS ROASTS OF THIS BEAN (oldest first):", prior]
    if cupping_notes:
        sections += ["", "ROASTER'S OWN CUPPING NOTES (this roast):", cupping_notes]
    sections += [
        "",
        "Analyze this roast and return recommendations and next-roast actions.",
    ]
    return "\n".join(sections)


def generate_llm_recommendations(metrics, data, bean_profile=None,
                                 cupping_notes=None, warnings=None,
                                 prior_roasts=None):
    """Generate recommendations + next-roast actions via Claude.

    Args:
        metrics: Dict from extract_metrics() (visual fields merged if present).
        data: Extracted roast data — used to reconstruct the control timeline,
            the BT/RoR curve, and the operator's Artisan-recorded notes/flags.
        bean_profile: Optional bean profile dict.
        cupping_notes: Optional override for the roaster's cupping notes —
            notes added via the `cupping` command live in history, not the
            .alog, and take precedence when provided.
        warnings: Optional list of data-quality warnings from validate_metrics().
        prior_roasts: Optional list of compact prior-roast dicts (same bean)
            from roast_analysis.select_prior_roasts().

    Returns:
        (result, status) where result is
        {"recommendations": [...], "next_roast": [...]} on success, or None on
        failure; status is a short human-readable string for the scan log.
    """
    try:
        import anthropic
    except ImportError:
        return None, "anthropic SDK not installed (uv add anthropic)"

    timeline = build_control_timeline(data)
    narrative_text = format_narrative(timeline)
    user_content = _build_user_content(
        metrics, data, bean_profile, narrative_text,
        cupping_notes=cupping_notes or data.get("cupping_notes", ""),
        warnings=warnings,
        prior_roasts=prior_roasts,
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            # Covers adaptive thinking (which counts against max_tokens) plus
            # the JSON output; 8000 risked truncation at effort "high".
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": _OUTPUT_SCHEMA},
            },
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_content}],
        )
    except anthropic.AuthenticationError:
        return None, "no API credentials (set ANTHROPIC_API_KEY via the wrapper)"
    except anthropic.APIError as e:
        return None, f"LLM API error: {e}"
    except Exception as e:  # network, unexpected SDK errors — fail soft
        return None, f"LLM call failed: {e}"

    if response.stop_reason == "refusal":
        return None, "model declined to analyze this roast"

    # Truncated output means broken JSON — report the real cause instead of
    # letting it surface as a misleading parse failure.
    if response.stop_reason == "max_tokens":
        return None, "output truncated at max_tokens — raise max_tokens in llm_recommender.py"

    # With adaptive thinking the first block may be a thinking block; the
    # json_schema format guarantees the text block is valid JSON.
    text = next((b.text for b in response.content if b.type == "text"), None)
    if not text:
        return None, "empty response from model"

    try:
        result = json.loads(text)
    except ValueError:
        return None, "could not parse model output as JSON"

    result.setdefault("recommendations", [])
    result.setdefault("next_roast", [])
    return result, "ok"
