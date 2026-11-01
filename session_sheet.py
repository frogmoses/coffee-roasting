"""Session sheet: the printable roast-day page for one profile version.

Generated from the profile (schedule, predictions, contrast set), the
dossier, the envelope, and the exported alarm rows, so it can never drift
from what the roaster was actually told to do (the previous hand-written
sheet under docs/ went stale within a session). Written to
`beans/<slug>/sheet-vN.html`; `export-alarms` regenerates it, `sheet` too.

The page is static HTML with the styling of the earlier sheet (paper and
coil colours, mono tables, print rules) and reads in this order: before the
first charge (alarm set, ear, warm-up, the CONTROL rule), the schedule,
what Artisan does on its own, the batches, first-crack marking, the table
(Hario Switch recipe, triangle test, intake), and the commands to feed it
back.
"""

import html
from datetime import datetime

from roast_schedule import schedule_rows, fmt_mmss, project_drop_bt
from roast_profile import sheet_path, profile_dir
from alarm_export import describe_rows
from roast_metrics import SAFETY_EJECT_BT

FONTS = '''<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans:ital,wght@0,400;0,500;1,400&family=IBM+Plex+Mono:wght@400;500&display=swap">'''

CSS = '''<style>
  :root {
    --paper: #F6F5EF;
    --paper-2: #ECEBE2;
    --ink: #1F211C;
    --ink-2: #4E5148;
    --rule: #C7C8BC;
    --coil: #B4542A;
    --coil-soft: #F1DCCF;
    --bean: #5F6E46;
    --bean-soft: #DCE2CC;
    --warn: #8C2F1D;
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      --paper: #191A17;
      --paper-2: #232521;
      --ink: #E9E7DD;
      --ink-2: #A9AB9F;
      --rule: #3C3E37;
      --coil: #E48451;
      --coil-soft: #3A2A21;
      --bean: #A3B27E;
      --bean-soft: #2A3122;
      --warn: #E58A76;
    }
  }
  :root[data-theme="dark"] {
    --paper: #191A17;
    --paper-2: #232521;
    --ink: #E9E7DD;
    --ink-2: #A9AB9F;
    --rule: #3C3E37;
    --coil: #E48451;
    --coil-soft: #3A2A21;
    --bean: #A3B27E;
    --bean-soft: #2A3122;
    --warn: #E58A76;
  }

  body {
    background: var(--paper);
    color: var(--ink);
    font-family: "IBM Plex Sans", "Helvetica Neue", Arial, sans-serif;
    font-size: 15.5px;
    line-height: 1.5;
  }
  .sheet {
    max-width: 46rem;
    margin: 0 auto;
    padding: 2.5rem 1.5rem 4rem;
  }
  h1, h2, h3 {
    font-family: "Bricolage Grotesque", "Arial Narrow", "Helvetica Neue", sans-serif;
    text-wrap: balance;
    margin: 0;
  }
  h1 { font-size: 2.4rem; font-weight: 700; line-height: 1.05; letter-spacing: -0.01em; }
  h2 { font-size: 1.35rem; font-weight: 700; line-height: 1.2; }
  h3 { font-size: 1.05rem; font-weight: 700; }
  p { margin: 0; max-width: 66ch; }
  .eyebrow {
    font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
    font-size: 0.72rem;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-2);
  }
  header { display: grid; gap: 0.6rem; padding-bottom: 1.5rem; border-bottom: 2px solid var(--ink); }
  header .meta { display: flex; flex-wrap: wrap; gap: 0.4rem 1.6rem; color: var(--ink-2); font-size: 0.92rem; }
  header .meta b { color: var(--ink); font-weight: 500; }

  section { display: grid; gap: 0.9rem; padding: 1.6rem 0; border-bottom: 1px solid var(--rule); }
  section:last-of-type { border-bottom: 0; }
  .section-head { display: grid; gap: 0.25rem; }

  .qa { display: grid; gap: 1rem; }
  .qa .q { font-weight: 500; }
  .qa .a { color: var(--ink-2); }
  .qa .a strong { color: var(--ink); font-weight: 500; }

  .steps { display: grid; gap: 0.6rem; margin: 0; padding-left: 1.4rem; }
  .steps li { padding-left: 0.2rem; }
  .steps li::marker { font-family: "IBM Plex Mono", monospace; color: var(--coil); font-weight: 500; }

  .tablewrap { overflow-x: auto; }
  table {
    border-collapse: collapse;
    width: 100%;
    font-family: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;
    font-size: 0.86rem;
    font-variant-numeric: tabular-nums;
  }
  th, td { text-align: left; padding: 0.42rem 0.7rem; border-bottom: 1px solid var(--rule); vertical-align: top; }
  th { font-weight: 500; color: var(--ink-2); font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; }
  td.num, th.num { text-align: right; }
  tr.fc td { border-top: 2px solid var(--coil); }
  .tag {
    display: inline-block;
    padding: 0.05rem 0.45rem;
    border-radius: 2px;
    font-size: 0.72rem;
    letter-spacing: 0.04em;
  }
  .tag.coil { background: var(--coil-soft); color: var(--coil); }
  .tag.bean { background: var(--bean-soft); color: var(--bean); }

  .callout {
    border-left: 3px solid var(--coil);
    background: var(--paper-2);
    padding: 0.8rem 1rem;
    display: grid;
    gap: 0.3rem;
  }
  .callout .eyebrow { color: var(--coil); }

  .checklist { display: grid; gap: 0.55rem; margin: 0; padding: 0; list-style: none; }
  .checklist li { display: grid; grid-template-columns: 1.1rem 1fr; gap: 0.6rem; align-items: start; }
  .checklist .box { width: 0.85rem; height: 0.85rem; border: 1.5px solid var(--ink-2); margin-top: 0.3rem; }
  .checklist .opts { color: var(--ink-2); font-size: 0.9rem; }
  .checklist .opts span { white-space: nowrap; }
  .checklist .opts span + span::before { content: " · "; color: var(--rule); }

  .cards { display: grid; gap: 0.8rem; grid-template-columns: repeat(auto-fit, minmax(13rem, 1fr)); }
  .card { background: var(--paper-2); padding: 0.9rem 1rem; display: grid; gap: 0.35rem; }
  .card .big { font-family: "Bricolage Grotesque", sans-serif; font-size: 1.6rem; font-weight: 700; line-height: 1; }
  .card .big small { font-size: 0.9rem; font-weight: 500; color: var(--ink-2); }
  .card .note { color: var(--ink-2); font-size: 0.9rem; }
  .card.warn .big { color: var(--warn); }

  .steps pre { margin: 0.4rem 0 0; }
  pre {
    margin: 0;
    background: var(--paper-2);
    padding: 0.7rem 0.9rem;
    font-family: "IBM Plex Mono", monospace;
    font-size: 0.84rem;
    overflow-x: auto;
  }
  footer { padding-top: 1.4rem; color: var(--ink-2); font-size: 0.85rem; display: grid; gap: 0.3rem; }

  @media print {
    body { background: #fff; color: #000; font-size: 11pt; }
    .sheet { max-width: none; padding: 0; }
    section { break-inside: avoid; }
    a { color: inherit; text-decoration: none; }
  }
</style>'''


def _e(text):
    return html.escape("" if text is None else str(text))


def _fan_into_fc(sched):
    """The fan level the schedule carries into first crack: a post-FC fan
    move if the design has one, else the last pre-FC fan move, else the
    charge fan."""
    for mv in sched.get("post_fc", []):
        if mv.get("action") == "fan":
            return int(mv.get("value", 0))
    fans = [int(mv.get("value", 0)) for mv in sched.get("moves", []) if mv.get("action") == "fan"]
    return fans[-1] if fans else int((sched.get("charge") or {}).get("fan", 0))


def _fan_batches(profile, envelope, values):
    """A fan-after-FC bracket: every batch at the design's development, the
    fan moved by hand at the FCs mark on all but the design batch. Only a
    manual session can run it (one alarm set, one fan value), so the card
    says what the operator does at the mark."""
    sched = profile.get("schedule", {})
    dev = int(sched.get("drop", {}).get("seconds", 150))
    base = _fan_into_fc(sched)
    values = sorted(set(int(round(v)) for v in values) | {base})
    ordered = [base] + [v for v in values if v > base][::-1] + [v for v in values if v < base]
    drop_bt = project_drop_bt(sched, envelope)
    cards = []
    for i, v in enumerate(ordered, 1):
        if v == base:
            label, note, move = "planned", f"The planned roast: fan stays at {base}% through first crack. Roasted first, while the drum is coldest.", None
        else:
            label = f"fan {v}%"
            move = f"at your FCs mark, move the fan slider to {v}%"
            note = (f"Fan {v}% from first crack: more airflow through development, a slightly slower rise, "
                    "chaff and smoke cleared. Expect the finish cleaner or flatter; the cup decides.")
        cards.append({"order": i, "dev_s": dev, "label": label, "note": note, "fan_after_fc": v,
                      "manual_move": move, "drop_bt": round(drop_bt),
                      "margin": round(SAFETY_EJECT_BT - drop_bt)})
    return cards


def batches_from_profile(profile, envelope):
    """The contrast set as cards: the design's own value first, then the
    short and long ends (same order roast_plan uses, so batch 1 is roasted
    under the same cold-drum state as every earlier first-of-day batch).
    A `fan_after_fc` lever gives fan cards instead (see `_fan_batches`)."""
    lever = profile.get("contrast_lever") or {}
    sched = profile.get("schedule", {})
    dev = int(sched.get("drop", {}).get("seconds", 150))
    key = lever.get("key") or "dev_seconds"
    values = [v for v in (lever.get("values") or []) if v is not None]
    if key == "fan_after_fc" and values:
        return _fan_batches(profile, envelope, values)
    if key != "dev_seconds" or not values:
        values = [dev]
    values = sorted(set(int(round(v)) for v in values))
    if dev not in values:
        values.append(dev)
        values.sort()
    ordered = [dev] + [v for v in values if v < dev][::-1] + [v for v in values if v > dev]
    cards = []
    for i, v in enumerate(ordered, 1):
        trial = dict(sched, drop={"after": "FCs", "seconds": v})
        drop_bt = project_drop_bt(trial, envelope)
        if v == dev:
            label, note = "planned", "The planned development. Roasted first, while the drum is coldest."
        elif v < dev:
            label, note = "shorter", "Expect brighter, possibly sour or grassy: what under-developed tastes like on this bean."
        else:
            label, note = "longer", "Expect roastier, heavier, less bright."
        cards.append({"order": i, "dev_s": v, "label": label, "note": note,
                      "drop_bt": round(drop_bt), "margin": round(SAFETY_EJECT_BT - drop_bt)})
    if key != "dev_seconds" and lever.get("values"):
        cards = [dict(c, lever_note=f"{key}: {lever['values']}") for c in cards]
    return cards


def _confidence_words(confidence, borrowed_from=""):
    """The designer's confidence label in words for the header."""
    if confidence == "borrowed":
        return f"estimated from your {borrowed_from} roasts" if borrowed_from else "estimated from another bean"
    if confidence == "measured":
        return "from this bean's roasts"
    if confidence == "guess":
        return "a guess"
    return confidence or ""


def _grouped_warnings(warnings):
    """Validator warnings with one line per kind: "Heater 90% at 262F, Heater
    80% at 281F: a single 10% heater step ..." instead of one line per move."""
    grouped, order = {}, []
    for w in warnings:
        who, sep, what = w.partition(": ")
        key = what if sep else w
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        if sep:
            grouped[key].append(who)
    return [(", ".join(grouped[k]) + ": " + k) if grouped[k] else k for k in order]


def render_sheet(profile, dossier, envelope, alarm_rows=None, batches=None, alarm_file=None):
    """The sheet as an HTML string."""
    sched = profile.get("schedule", {})
    bean = profile.get("bean", "?")
    version = profile.get("version", "?")
    pred = profile.get("predicted") or sched.get("predicted") or {}
    exported = profile.get("exported") or {}
    manual_drop = bool(exported.get("manual_drop", True))
    # A manual session times the drop from the operator's own mark; the
    # ear's latency only matters when the ear marks FCs and a DROP row runs
    # from that mark.
    latency = 0 if manual_drop else (exported.get("ear_latency_s") or (envelope or {}).get("ear_latency_s") or 0)
    mode = exported.get("mode") or "paired"
    dev = int(sched.get("drop", {}).get("seconds", 150))
    batches = batches if batches is not None else batches_from_profile(profile, envelope)
    warm = (sched.get("charge") or {}).get("warmup") or {}
    has_warmup_rows = any(r.get("from") == 9 for r in (alarm_rows or []))
    rows = schedule_rows(sched, envelope)
    fc_time = pred.get("fc_time_s")
    stats = (envelope or {}).get("fc_stats") or {}

    out = []
    a = out.append
    a(f"<title>{_e(bean)} v{_e(version)}</title>")
    a(FONTS)
    a(CSS)
    borrowed_from = (envelope or {}).get("borrowed_from") or ""
    a('<div class="sheet">')
    # -- header
    a("<header>")
    a('<div class="eyebrow">Roast day sheet</div>')
    a(f"<h1>{_e(bean)} <small style=\"font-size:1rem;color:var(--ink-2)\">profile v{_e(version)}</small></h1>")
    hyp = profile.get("flavor_hypothesis") or ""
    rationale = profile.get("rationale") or ""
    if rationale or hyp:
        a(f"<p>{_e(rationale)}" + (f" <strong>Expect in the cup:</strong> {_e(hyp.rstrip('. '))}." if hyp else "") + "</p>")
    a('<div class="meta">')
    if fc_time:
        a(f"<span>Predicted FC <b>~{_e(fmt_mmss(fc_time))} at {_e(pred.get('fc_bt', '?'))}F</b>"
          + (f" ({_e(_confidence_words(pred.get('confidence'), borrowed_from))})" if pred.get("confidence") else "")
          + "</span>")
    if stats.get("count"):
        whose = f"{borrowed_from} first crack" if borrowed_from else "First crack so far"
        a(f"<span>{_e(whose)} <b>{_e(fmt_mmss(stats['fc_time_min']))} to {_e(fmt_mmss(stats['fc_time_max']))}</b> over {stats['count']} roasts</span>")
    a(f"<span>Development <b>FC + {_e(fmt_mmss(dev))}</b>, drop ~{_e(pred.get('drop_bt', '?'))}F</span>")
    a(f"<span>Safety eject <b>{SAFETY_EJECT_BT}F</b></span>")
    if alarm_file:
        a(f"<span>Alarm set <b>{_e(alarm_file)}</b></span>")
    if borrowed_from:
        a(f"<span>First roast of this bean: times come from your <b>{_e(borrowed_from)}</b> roasts</span>")
    a("</div></header>")

    # -- before the first charge
    a("<section>")
    a('<div class="section-head"><div class="eyebrow">Before the first charge</div><h2>Load the set, start the ear, let the warm-up run</h2></div>')
    a('<ol class="steps">')
    a("<li>Before Artisan opens on the roaster, its settings must match the copy kept on your dev machine (that copy has the Control On button and Artisan's quantifiers off): "
      "<code>DEPLOY_SSH_HOST=roaster artisan/deploy.sh diff</code> prints nothing; if it does, <code>push</code> with Artisan closed, then start Artisan.</li>")
    a(f"<li>Check the microphone: <code>run_ear ear.py level</code> with the empty drum running. Peaks near -30 dBFS and no clipping; "
      "snap your fingers by the capsule and the cracks column climbs. Adjust the capture gain in <code>alsamixer -c 1</code> until it does.</li>")
    a(f"<li>Start the ear on the roaster: <code>run_ear ear.py listen --bean \"{_e(bean)}\"</code>. One listen covers the day.</li>")
    a(f"<li>Name the roast in Artisan (<strong>{_e(bean)}</strong>) before you press ON, so the batch groups with its bean.</li>")
    if alarm_file:
        a(f"<li>Config &rarr; Alarms &rarr; Load <code>{_e(alarm_file)}</code>. Check the table matches the list below.</li>")
    if has_warmup_rows:
        a("<li>ON. The first alarm row takes Hottop control (super-user comes with it) and starts the drum; the next closes the door, stops the stirrer, "
          "and sets heater 100% and fan 0%. <strong>Do not press CONTROL yourself, today or ever</strong>: releasing control turns the Hottop off.</li>")
        a(f"<li>The alarms run the warm-up: heater 100% until BT reaches <strong>{_e(warm.get('preheat_bt', 370))}F</strong>, "
          f"then heater {_e(warm.get('cool_heater', 0))}% and fan {_e(warm.get('cool_fan', 100))}% until BT falls to "
          f"<strong>{_e(warm.get('charge_bt', 300))}F</strong>, then fan 0% and heater 100% with a pop-up that says charge. "
          "Charge as soon as it does; write the ET reading on the batch's intake line (it is a record, not a thing to wait for).</li>")
    else:
        a("<li>ON, then right-click the timer LCD until the message bar says <strong>super on</strong>, then CONTROL once (<strong>Hottop control turned on</strong>). "
          "<strong>Never press CONTROL again today</strong>: releasing control turns the Hottop off.</li>")
        a(f"<li>Warm up by hand: drum motor on, heater 100% and overshoot to about <strong>{_e(warm.get('preheat_bt', 370))}F</strong> BT; "
          f"then heater {_e(warm.get('cool_heater', 0))}%, fan {_e(warm.get('cool_fan', 100))}%, let BT fall to <strong>{_e(warm.get('charge_bt', 300))}F</strong>; "
          "heater back to 100%, fan 0%, and charge. Note the ET reading at that moment.</li>")
    a("<li>Press START right before you charge, then CHARGE. From here the alarms make every move through first crack. "
      "If the timer is not running you have not pressed START and no alarm is watching: DROP and start over.</li>")
    a("<li><strong>DROP is the panic button.</strong> Never OFF during a roast: with recording off no alarm evaluates, the 398F drop included, "
      "and the heater stays where it was.</li>")
    a("<li>Between batches, in this order: DROP; <strong>COOL END</strong> (always, before OFF: it closes the door and stops the drum, fan, and stirrer; "
      "after OFF nothing will); OFF; <strong>save the log</strong> (the ear waits for that save before it can start the next batch's session); then ON. "
      "The warm-up repeats on its own.</li>")
    a("</ol></section>")

    # -- schedule
    a("<section>")
    a('<div class="section-head"><div class="eyebrow">Schedule</div><h2>Every batch, from charge to first crack</h2></div>')
    a('<div class="tablewrap"><table><thead><tr><th>When</th><th class="num">Expect BT</th><th>Move</th><th>Trigger</th><th>Why</th></tr></thead><tbody>')
    for r in rows:
        cls = ' class="fc"' if r["phase"] == "drop" else ""
        bt = f"{r['bt']:.0f}F" if r.get("bt") is not None else ""
        kind = {"bt": "bean temp", "time": "clock", "manual": "you"}.get(r["kind"], r["kind"])
        a(f"<tr{cls}><td>{_e(r['when'])}</td><td class=\"num\">{_e(bt)}</td><td>{_e(r['text'])}</td>"
          f"<td><span class=\"tag {'bean' if r['kind'] == 'bt' else 'coil'}\">{_e(kind)}</span></td><td>{_e(r.get('why', ''))}</td></tr>")
    a("</tbody></table></div>")
    if mode == "paired":
        a("<p class=\"a\" style=\"color: var(--ink-2)\">Times are expected, from the measured curve; a bean-temp trigger fires when BT gets there, "
          "or at the fallback time if it lags. On a cool day the clock moves land early in BT and first crack comes later; that is the weather, not the plan.</p>")
    elif mode == "bt":
        a("<p class=\"a\" style=\"color: var(--ink-2)\">Times are expected, from the measured curve; the heater cuts fire on bean temperature only, "
          "so every batch gets the same cut at the same BT however hot the drum still is from the last batch or whatever the weather does to the clock. "
          "On a cool day everything simply comes later.</p>")
    else:
        a("<p class=\"a\" style=\"color: var(--ink-2)\">Every move fires on the clock from CHARGE; the BT column is what the measured curve expects. "
          "On a cool day the cuts land at a lower BT and first crack comes later; that is the weather, not the plan.</p>")
    a("</section>")

    # -- what Artisan does on its own
    if alarm_rows:
        a("<section>")
        a('<div class="section-head"><div class="eyebrow">What Artisan does on its own</div><h2>The alarm table, row by row</h2></div>')
        a("<pre>" + _e("\n".join(describe_rows(alarm_rows))) + "</pre>")
        a("<p class=\"a\" style=\"color: var(--ink-2)\">* beeps. [if #N] fires only after row N; [unless #N] only if row N has not fired. "
          "A row with both a BT and a time fires on whichever comes first.</p>")
        a("</section>")

    # -- batches
    a("<section>")
    a('<div class="section-head"><div class="eyebrow">The batches, in roasting order</div><h2>The planned development first, then a shorter and a longer one</h2></div>')
    a('<div class="cards">')
    for c in batches:
        warn = ' warn' if c["margin"] < 10 else ""
        a(f'<div class="card{warn}"><div class="eyebrow">Batch {c["order"]} &middot; {_e(c["label"])}</div>'
          f'<div class="big">FC + {_e(fmt_mmss(c["dev_s"]))} <small>{c["dev_s"]} s &middot; drop ~{c["drop_bt"]}F</small></div>'
          + (f'<div class="big" style="font-size:1.1rem">At FCs: fan &rarr; {c["fan_after_fc"]}%</div>' if c.get("manual_move") else "")
          + f'<div class="note">{_e(c["note"])}' + (" If BT reaches 400F before the timer, drop." if c["margin"] < 15 else "") + "</div></div>")
    a("</div>")
    lever = profile.get("contrast_lever") or {}
    if lever.get("why"):
        a(f"<p class=\"a\" style=\"color: var(--ink-2)\">{_e(lever['why'])}</p>")
    a("</section>")

    # -- first crack
    a("<section>")
    a('<div class="section-head"><div class="eyebrow">First crack</div><h2>Mark it, then the timer runs</h2></div>')
    a('<ol class="steps">')
    if manual_drop:
        a("<li><strong>You mark FCs</strong>, at the first distinct crack or at 363F BT, whichever comes first. The ear's bell is confirmation, "
          "about 18 s behind the first cracks; if it rings before you have marked, mark now. The 372F pop-up means you have missed it: mark at once.</li>")
        for c in batches:
            move = f" <strong>{_e(c['manual_move'])}</strong>, then" if c.get("manual_move") else ""
            a(f"<li>Batch {c['order']}:{move} start a stopwatch at <em>your</em> FCs mark and press <strong>DROP at {_e(fmt_mmss(c['dev_s']))}</strong> "
              f"(that is {c['dev_s']} seconds, read the stopwatch as minutes:seconds).</li>")
        a("<li>Drop early if BT reaches 400F; the 398F alarm will do it for you.</li>")
    else:
        a("<li><strong>The ear marks FCs; keep your hands off the FCs button.</strong> The DROP row is timed from the ear's mark minus its "
          f"{latency:.0f} s latency, so a hand mark would shorten development by that much. The ear must be running with <code>--push-artisan</code>.</li>")
        a(f"<li>The DROP alarm fires <strong>{_e(fmt_mmss(dev))}</strong> of development after first crack. "
          "For the shorter and longer batches load the matching set or drop by hand at the card's time.</li>")
        a("<li>If the 372F pop-up fires with no FCs mark, the ear missed it: mark FCs yourself and press DROP at the card's time minus "
          f"{latency:.0f} s on your stopwatch (the row will fire {latency:.0f} s early from your mark; beat it or accept it).</li>")
    for w in profile.get("watch_for") or []:
        # In a manual session the designer's lines about the ear's lag would
        # put the stopwatch 18 s off the step above; leave them out.
        if manual_drop and any(word in w.lower() for word in ("latency", "lag", "ear's mark", "machine's fcs")):
            continue
        a(f"<li>Watch for: {_e(w)}</li>")
    a("</ol></section>")

    # -- at the table
    a("<section>")
    a('<div class="section-head"><div class="eyebrow">At the table</div><h2>Taste for difference, not for words</h2></div>')
    a('<ol class="steps">')
    a("<li><strong>Rest 3 to 5 days</strong> before judging. At 24 hours every batch tastes flat and gassy in the Switch.</li>")
    a("<li>Brew all batches in the Hario Switch the same way, back to back, cups labeled by batch number only. Recipe: <strong>15 g</strong> medium grind, "
      "<strong>250 g at 94°C</strong>, switch closed; pour 50 g and swirl at 0:00, fill to 250 g at 0:45, open the switch at 2:00, drained by about 3:00. "
      "Hot water throughout: no cool second pour, since that hides the roasty end of the scale.</li>")
    a("<li>Taste each cup hot at about four minutes and again ten minutes later. Sourness, astringency, and whether sweetness holds all show up in the cooler cup.</li>")
    a("<li><strong>Triangle first.</strong> Pour two cups of one batch and one of another, shuffle, and pick the odd one out. "
      "If you can, you have discrimination and only lack vocabulary. If you cannot, the batches were too close together, not your palate at fault.</li>")
    a("<li>Then taste in roasting order and answer the intake below for each. Preference is the signal. Naming why is optional.</li>")
    a("</ol>")
    a("<h3>Intake, one per batch</h3>")
    a('<ul class="checklist">')
    for q, opts in (("Sour or bright ... or bitter or roasty?", ["very sour", "a little sour", "balanced", "a little roasty", "very roasty / ashy"]),
                    ("Astringent, drying, aspirin-like?", ["clean", "slight drying", "noticeable", "harsh"]),
                    ("Sweetness?", ["none", "a hint", "clearly sweet", "syrupy"]),
                    ("Body?", ["thin", "medium", "heavy"]),
                    ("Compared with the previous batch?", ["worse", "same", "better"]),
                    ("Would you happily drink it again?", ["yes", "it's fine", "no"])):
        a('<li><span class="box"></span><div>' + _e(q) + '<div class="opts">' + "".join(f"<span>{_e(o)}</span>" for o in opts) + "</div></div></li>")
    a("</ul>")
    if hyp:
        a(f"<p class=\"a\" style=\"color: var(--ink-2)\">This profile should taste like: {_e(hyp.rstrip('. '))}. If the cup disagrees, that is what we learn.</p>")
    a("</section>")

    # -- afterward
    a("<section>")
    a('<div class="section-head"><div class="eyebrow">Afterward</div><h2>Feed it back</h2></div>')
    cmds = ["run_roast-analyzer analyze.py full          # read the logs; each batch shows the plan next to what the roast actually did"]
    cmds += [f"run_roast-analyzer analyze.py cupping <batch> --intake      # once per batch, after resting"]
    cmds += [f"run_roast-analyzer analyze.py profile \"{bean}\" --revise    # the next version from the cups",
             f"run_roast-analyzer analyze.py export-alarms \"{bean}\"      # its alarm set and sheet"]
    a("<pre>" + _e("\n".join(cmds)) + "</pre>")
    a("</section>")
    a(f"<footer><span>Generated {_e(datetime.now().strftime('%Y-%m-%d %H:%M'))} from profile v{_e(version)}"
      + (f", built from roast {_e(profile['based_on']['roast_id'])}" if (profile.get("based_on") or {}).get("roast_id") else "")
      + "</span>")
    for w in _grouped_warnings((profile.get("validator") or {}).get("warnings") or []):
        a(f"<span>Note: {_e(w)}</span>")
    a("</footer></div>")
    return "\n".join(out)


def write_sheet(slug, version, html_text):
    """Write the sheet next to the profile; returns the path."""
    profile_dir(slug, create=True)
    path = sheet_path(slug, version)
    path.write_text(html_text, encoding="utf-8")
    return path
