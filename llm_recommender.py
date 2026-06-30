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

from roast_metrics import _fmt_time
from roast_narrative import build_control_timeline, format_narrative
from hottop_reference import HOTTOP_CONTROLS

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

"""
    + HOTTOP_CONTROLS
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


def _build_user_content(metrics, bean_profile, narrative_text, cupping_notes=""):
    """Assemble the full analysis prompt body."""
    sections = [
        "KEY METRICS (facts of this roast — not compared to any target band):",
        json.dumps(_curated_metrics(metrics), indent=2, default=str),
        "",
        "CONTROL TIMELINE (the moves the roaster actually made, CHARGE->DROP):",
        narrative_text,
        "",
        "BEAN PROFILE (what this bean is supposed to taste like):",
        _bean_block(bean_profile),
    ]
    visual = _visual_block(metrics)
    if visual:
        sections += ["", "VISUAL DEVELOPMENT:", visual]
    if cupping_notes:
        sections += ["", "ROASTER'S OWN CUPPING NOTES:", cupping_notes]
    sections += [
        "",
        "Analyze this roast and return recommendations and next-roast actions.",
    ]
    return "\n".join(sections)


def generate_llm_recommendations(metrics, data, bean_profile=None):
    """Generate recommendations + next-roast actions via Claude.

    Args:
        metrics: Dict from extract_metrics() (visual fields merged if present).
        data: Extracted roast data — used to reconstruct the control timeline
            and to read the roaster's own cupping notes.
        bean_profile: Optional bean profile dict.

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
        metrics, bean_profile, narrative_text,
        cupping_notes=data.get("cupping_notes", ""),
    )

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=MODEL,
            max_tokens=8000,
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
