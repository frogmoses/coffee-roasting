#!/usr/bin/env python3
"""Coffee roast analysis CLI.

Parses Artisan .alog files, enriches with bean data from find-coffee,
compares metrics against targets, and generates actionable recommendations.

Usage:
    python analyze.py scan              # Analyze new .alog files
    python analyze.py show [roast_id]   # Show summary (default: latest)
    python analyze.py compare [id1 id2] # Compare two roasts
    python analyze.py recommend         # Recommendations for latest roast
    python analyze.py cupping <id> --notes "text"  # Add cupping notes
    python analyze.py cupping <id> --intake        # Guided cupping questions
    python analyze.py plan [id] --dev 150,90,210   # Contrast set for next session
    python analyze.py full [roast_id]   # Everything: scan + summary + recommendations
    python analyze.py list              # List all analyzed roasts
    python analyze.py bean <name>       # Look up a bean in find-coffee
    python analyze.py dossier <bean> [--research]  # Research the bean (find-coffee + supplier page)
    python analyze.py profile <bean> --design      # The designer writes the bean's roast profile
    python analyze.py profile <bean> --revise      # Next version from the cupping verdicts
    python analyze.py profile <bean> [--from-roast ID]   # Show / build a version from a logged roast
    python analyze.py export-alarms <bean> [--manual-drop]  # Artisan alarm set + session sheet
    python analyze.py sheet <bean>                 # Regenerate the printable session sheet
"""

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from roast_parser import parse_alog, extract_roast_data, scan_roast_logs
from roast_analysis import (
    analyze_roast,
    compare_roasts,
    refresh_recommendations,
    select_prior_roasts,
)
from cupping_intake import run_intake, normalize_intake, intake_to_text
from roast_plan import build_contrast_plan, parse_dev_times, DEFAULT_DEV_TIMES
from roast_display import (
    format_time,
    display_contrast_plan,
    display_roast_summary,
    display_bean_profile,
    display_recommendations,
    display_roast_comparison,
    display_trend,
    display_roast_list,
    display_next_roast,
)
from coffee_lookup import lookup_bean, extract_bean_profile, ensure_server_running, stop_server
from sentinel_loader import match_sentinel_to_roast, extract_visual_data, enrich_trajectory_with_temps
from crack_loader import match_crack_to_roast, extract_audio_data
from roast_narrative import build_control_timeline
from roast_schedule import schedule_from_timeline, validate_schedule, compare_execution
from machine_envelope import envelope_for, compact_envelope
from alarm_export import schedule_to_alarms, write_alrm, rows_to_conf
from roast_profile import (
    slugify, find_bean_title, load_profile, save_profile, new_profile, mark_exported,
    set_reference, get_reference, attach_roast, match_profile_to_roast, alarm_path,
    list_versions,
)
from roast_display import display_profile, display_alarm_rows, display_execution
from bean_dossier import (
    find_dossier_for_title, build_dossier, load_dossier, save_dossier, add_alias, research_bean,
)
from profile_designer import design_profile, revise_profile
from session_sheet import render_sheet, write_sheet, batches_from_profile
from roast_profile import sheet_path, attach_verdict
from roast_schedule import schedule_from_envelope
from llm_client import MODEL as LLM_MODEL

# Paths
PROJECT_DIR = Path(__file__).parent
LOGS_DIR = PROJECT_DIR / "roast-logs"
HISTORY_FILE = PROJECT_DIR / "roast_history.json"


def load_history():
    """Load analysis history from disk."""
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text())
    return {}


def save_history(history):
    """Save analysis history to disk atomically.

    Writes to a temp file then renames, so a crash mid-write can't
    corrupt the only copy of the history (it's gitignored).
    """
    tmp_path = HISTORY_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(history, indent=2, default=str))
    os.replace(tmp_path, HISTORY_FILE)


def get_sorted_analyses(history):
    """Get all analyses sorted by date."""
    analyses = list(history.values())
    analyses.sort(key=lambda a: (a.get("roast_date", ""), a.get("batch_nr", 0)))
    return analyses


def resolve_roast_id(history, roast_id=None):
    """Resolve a roast ID, defaulting to latest.

    Accepts full roast_id, batch number, or partial match.
    """
    if not history:
        return None

    if roast_id is None:
        # Default to latest
        analyses = get_sorted_analyses(history)
        return analyses[-1]["roast_id"] if analyses else None

    # Try exact match
    if roast_id in history:
        return roast_id

    # Try batch number match
    for rid, data in history.items():
        if str(data.get("batch_nr", "")) == str(roast_id):
            return rid

    # Try partial name match — prefer the most recent roast so e.g.
    # "Ethiopia" resolves to the latest Ethiopia, not the first scanned
    matches = [rid for rid in history if roast_id.lower() in rid.lower()]
    if matches:
        matches.sort(key=lambda rid: (
            history[rid].get("roast_date", ""),
            history[rid].get("batch_nr", 0),
        ))
        return matches[-1]

    return None


def cmd_scan(args):
    """Scan for new .alog files and analyze them."""
    history = load_history()
    alog_files = scan_roast_logs(LOGS_DIR)

    if not alog_files:
        print(f"No .alog files found in {LOGS_DIR}")
        return

    # Start find-coffee server once for the whole scan batch
    server_ok, server_status = ensure_server_running()
    print(f"  find-coffee: {server_status}")

    new_count = 0
    for alog_path in alog_files:
        # Parse the file — one corrupt .alog shouldn't block the whole scan
        try:
            raw = parse_alog(alog_path)
            data = extract_roast_data(raw)
        except (ValueError, FileNotFoundError, KeyError, IndexError, TypeError) as e:
            print(f"  !! Skipping {alog_path.name}: {e}")
            continue
        roast_id = data["roast_id"]

        # Disambiguate ID collisions: two roasts with the same batch number,
        # title, and date (e.g. duplicate batch numbers) would otherwise
        # silently overwrite each other in history
        existing = history.get(roast_id)
        if (existing and existing.get("source_file")
                and existing["source_file"] != str(alog_path)):
            suffix = data.get("roast_time", "").replace(":", "")
            if suffix:
                roast_id = f"{roast_id}_{suffix}"
                data["roast_id"] = roast_id

        # Skip if already analyzed (unless --force)
        if roast_id in history and not getattr(args, "force", False):
            print(f"  Already analyzed: {roast_id}")
            continue

        # Bean context: the dossier when the bean has one (roast range,
        # process, research), else a live find-coffee lookup
        bean_profile = None
        bean_name = data.get("title", "")
        dossier = find_dossier_for_title(bean_name) if bean_name else None
        if dossier:
            bean_profile = {k: v for k, v in dossier.items()
                            if k not in ("page_excerpt", "versions")}
            print(f"  Bean dossier: {dossier['name']}")
        elif bean_name and server_ok:
            print(f"  Looking up bean: {bean_name}...")
            coffee_data, lookup_status = lookup_bean(bean_name)
            if coffee_data:
                bean_profile = extract_bean_profile(coffee_data)
                print(f"  Found bean profile for: {bean_profile['name']}")
            else:
                print(f"  Bean lookup: {lookup_status}")

        # Look for matching sentinel visual data (GoPro or r1-eye)
        visual_data = None
        roast_date = data.get("roast_date", "")
        roast_time = data.get("roast_time", "")
        roast_uuid = data.get("roast_uuid", "")
        if roast_date or roast_uuid:
            sentinel = match_sentinel_to_roast(roast_date, roast_time, roast_uuid)
            if sentinel:
                visual_data = extract_visual_data(sentinel)
                if visual_data:
                    # Enrich trajectory with .alog BT/ET temperatures
                    enrich_trajectory_with_temps(visual_data, data)
                    source = visual_data.get("visual_source", "Sentinel")
                    print(f"  Found {source} visual data: {visual_data['score_count']} captures")

        # Look for the ear's crack sidecar (microphone first-crack detector)
        audio_data = None
        if roast_date or roast_uuid:
            crack = match_crack_to_roast(roast_date, roast_time, roast_uuid)
            audio_data = extract_audio_data(crack, data)
            if audio_data:
                fc_txt = (f"FC by audio at {format_time(audio_data['detected_time'])}"
                          if audio_data.get("detected_time") is not None else "no FC declared")
                print(f"  Found audio crack data: {audio_data['crack_count']} cracks, {fc_txt}")

        # Cupping notes added via the 'cupping' command live only in history —
        # pass them INTO the analysis (not just restore them after) so a
        # --force re-scan lets the LLM judge the roast against how it tasted
        prior_entry = history.get(roast_id)
        preserved_notes = (prior_entry or {}).get("cupping_notes", "")

        # Earlier roasts of the same bean give the LLM the dial-in history
        prior_roasts = select_prior_roasts(
            history, roast_id, data.get("title", ""),
            data.get("roast_date", ""), data.get("batch_nr", 0),
        )

        # The profile version exported before this roast, if the bean has one
        prof = _profile_for_roast(data)

        # Run analysis
        analysis = analyze_roast(
            data, bean_profile, visual_data,
            cupping_notes=preserved_notes or None,
            prior_roasts=prior_roasts,
            audio_data=audio_data,
            profile=prof,
        )
        analysis["source_file"] = str(alog_path)
        if prof:
            _link_profile(prof, analysis)
        # The structured intake lives only in history too — carry it over so
        # a --force re-scan doesn't drop the answers behind the notes text
        if (prior_entry or {}).get("cupping_intake"):
            analysis["cupping_intake"] = prior_entry["cupping_intake"]

        history[roast_id] = analysis
        new_count += 1
        print(f"  Analyzed: {roast_id}")
        # Surface LLM recommender status — a non-"ok" status means the metrics
        # were saved but recommendations were skipped.
        llm_status = analysis.get("llm_status")
        if llm_status and llm_status != "ok":
            print(f"    !! recommendations skipped: {llm_status}")
        # Print data quality warnings inline during scan
        for warning in analysis.get("warnings", []):
            print(f"    !! {warning}")

    # Clean up find-coffee server if we started it
    stop_server()

    save_history(history)
    print(f"\nScanned {len(alog_files)} files, {new_count} new analyses saved.")


def cmd_show(args):
    """Show summary of a roast."""
    history = load_history()
    roast_id = resolve_roast_id(history, args.roast_id)

    if not roast_id:
        print("Roast not found. Run 'scan' first or check the ID.")
        return

    analysis = history[roast_id]
    print(display_roast_summary(analysis))

    # Show bean profile if available
    if analysis.get("bean_profile"):
        print()
        print(display_bean_profile(analysis["bean_profile"]))


def cmd_compare(args):
    """Compare two roasts side by side."""
    history = load_history()
    analyses = get_sorted_analyses(history)

    if len(analyses) < 2:
        print("Need at least 2 analyzed roasts to compare.")
        return

    # Resolve IDs. A given-but-unresolvable ID is an error — silently
    # substituting the latest roasts would compare the wrong pair.
    if args.id1 is not None:
        id1 = resolve_roast_id(history, args.id1)
        if not id1:
            print(f"Roast not found: {args.id1}")
            return
    else:
        id1 = analyses[-2]["roast_id"]

    if args.id2 is not None:
        id2 = resolve_roast_id(history, args.id2)
        if not id2:
            print(f"Roast not found: {args.id2}")
            return
    else:
        id2 = analyses[-1]["roast_id"]

    a1 = history[id1]
    a2 = history[id2]
    changes = compare_roasts(a1, a2)
    print(display_roast_comparison(changes, a1.get("title", id1), a2.get("title", id2)))


def cmd_recommend(args):
    """Show recommendations for the latest roast."""
    history = load_history()
    roast_id = resolve_roast_id(history, getattr(args, "roast_id", None))

    if not roast_id:
        print("No roasts analyzed. Run 'scan' first.")
        return

    analysis = history[roast_id]
    verbose = getattr(args, "verbose", False)
    print(display_recommendations(analysis.get("recommendations", []), verbose=verbose))

    # Next roast synthesis (generated by the LLM at scan time, cached in history)
    actions = analysis.get("next_roast", [])
    if actions:
        print()
        print(display_next_roast(actions))


def cmd_cupping(args):
    """Add or update cupping notes for a roast.

    Three ways in: --notes (free text), --intake (guided questions on the
    terminal), or --intake-json (the same answers as a JSON object, for
    scripts). The intake is stored structured (`cupping_intake`) AND rendered
    into `cupping_notes`, so the LLM flavor loop reads it either way; free
    text given alongside an intake is appended as its "notes" answer.
    """
    history = load_history()
    roast_id = resolve_roast_id(history, args.roast_id)

    if not roast_id:
        print("Roast not found.")
        return

    entry = history[roast_id]
    notes = getattr(args, "notes", None)
    intake = None
    intake_json = getattr(args, "intake_json", None)
    if intake_json:
        intake = normalize_intake(json.loads(intake_json))
    elif getattr(args, "intake", False):
        intake = run_intake()
        if not intake:
            print("No answers given — nothing saved.")
            return

    if intake is not None:
        if notes:
            # Free text alongside the intake becomes its notes answer
            intake["notes"] = (intake.get("notes", "") + " " + notes).strip()
        entry["cupping_intake"] = intake
        entry["cupping_notes"] = intake_to_text(intake)
    elif notes:
        entry["cupping_notes"] = notes
    else:
        current = entry.get("cupping_notes", "")
        if current:
            print(f"Current notes: \"{current}\"")
            if entry.get("cupping_intake"):
                print(f"Intake: {json.dumps(entry['cupping_intake'])}")
        else:
            print("No cupping notes. Use --intake (guided) or --notes to add them.")
        return

    save_history(history)
    print(f"Updated cupping notes for {roast_id}:")
    print(f"  \"{entry['cupping_notes']}\"")

    # Flavor (intended vs actual) is the primary judging signal, and the
    # scan-time recommendations were generated before the roast was tasted
    # — regenerate them now that we know how it cupped. Fails soft: the
    # notes are already saved, and the cached recommendations are kept.
    print("Regenerating recommendations with your cupping notes...")
    updated, status = refresh_recommendations(entry, history)
    if updated:
        save_history(history)
        print("Recommendations updated — run 'recommend' to view them.")
    else:
        print(f"  !! recommendations not refreshed: {status} (previous ones kept)")

    # The verdict belongs to the profile version the roast ran on; the next
    # version is designed from it with `profile <bean> --revise`
    ref = entry.get("profile_ref")
    if ref:
        prof = load_profile(ref.get("slug", ""), ref.get("version"))
        if prof and attach_verdict(prof, roast_id, intake=intake, notes=entry.get("cupping_notes", "")):
            save_profile(prof)
            cupped = sum(1 for r in prof.get("roasts", []) if r.get("cupping_intake") or r.get("cupping_notes"))
            print(f"Verdict recorded on profile v{prof['version']} ({cupped}/{len(prof['roasts'])} of its roasts cupped). "
                  f"When the session is cupped: profile \"{entry.get('title', '')}\" --revise")


def cmd_plan(args):
    """Print a contrast set (same recipe through FC, varied FC->DROP) for
    the bean of the given/latest roast."""
    history = load_history()
    roast_id = resolve_roast_id(history, getattr(args, "roast_id", None))
    if not roast_id:
        print("Roast not found.")
        return
    dev_text = getattr(args, "dev", None)
    dev_times = parse_dev_times(dev_text) if dev_text else DEFAULT_DEV_TIMES
    schedule = None
    if getattr(args, "profile", False):
        prof = load_profile(slugify(history[roast_id].get("title", "")))
        if prof:
            schedule = prof["schedule"]
            if not dev_text:
                dev = int(schedule.get("drop", {}).get("seconds", 150))
                dev_times = (dev, max(60, dev - 40), dev + 30)
            print(f"Recipe from profile v{prof['version']}")
        else:
            print("No profile for this bean; using the anchor roast's moves.")
    plan = build_contrast_plan(history, roast_id, dev_times=dev_times, schedule=schedule)
    print(display_contrast_plan(plan))


def cmd_full(args):
    """Full analysis: scan, show, recommend."""
    # First scan for new files
    cmd_scan(args)

    history = load_history()
    roast_id = resolve_roast_id(history, getattr(args, "roast_id", None))

    if not roast_id:
        print("No roasts to analyze.")
        return

    analysis = history[roast_id]

    # Summary
    print()
    print(display_roast_summary(analysis))

    # Bean profile
    if analysis.get("bean_profile"):
        print()
        print(display_bean_profile(analysis["bean_profile"]))

    # Designed vs executed, when the roast ran on a profile
    if analysis.get("execution"):
        print()
        print(display_execution(analysis["execution"]))

    # Recommendations
    verbose = getattr(args, "verbose", False)
    print()
    print(display_recommendations(analysis.get("recommendations", []), verbose=verbose))

    # Next roast synthesis (generated by the LLM at scan time, cached in history)
    actions = analysis.get("next_roast", [])
    if actions:
        print()
        print(display_next_roast(actions))

    # Trend (if multiple roasts)
    analyses = get_sorted_analyses(history)
    if len(analyses) >= 2:
        print()
        print(display_trend(analyses))


def cmd_list(args):
    """List all analyzed roasts."""
    history = load_history()
    analyses = get_sorted_analyses(history)
    print(display_roast_list(analyses))


def cmd_bean(args):
    """Look up a bean in find-coffee."""
    print(f"Looking up: {args.name}...")
    server_ok, server_status = ensure_server_running()
    if not server_ok:
        print(f"  find-coffee: {server_status}")
        return

    print(f"  find-coffee: {server_status}")
    coffee_data, lookup_status = lookup_bean(args.name)

    if not coffee_data:
        print(f"  Bean lookup: {lookup_status}")
        return

    profile = extract_bean_profile(coffee_data)
    print(display_bean_profile(profile))
    stop_server()


def _profile_for_roast(data):
    """The profile version exported before this roast (by the bean's title
    and the roast's date/time), or None."""
    title = data.get("title", "")
    if not title:
        return None
    return match_profile_to_roast(slugify(title), data.get("roast_date", ""),
                                  data.get("roast_time", ""))


def _link_profile(prof, analysis):
    """Record the roast (and its execution check) on the profile version."""
    attach_roast(prof, analysis, analysis.get("execution"))
    save_profile(prof)
    execution = analysis.get("execution") or {}
    print(f"  Profile v{prof['version']}: {execution.get('summary', 'linked')}")


def _bean_for(history, name):
    """(title, slug) for a CLI bean name; an unknown name is a new bean."""
    title = find_bean_title(history, name) or name.strip()
    return title, slugify(title)


def cmd_profile(args):
    """Show, build, or mark the bean's roast profile.

    `--from-roast ID` builds a version deterministically from that roast's
    logged moves (no LLM): the staircase becomes BT-triggered heater cuts
    and clock-triggered fan moves, the drop is the roast's own development
    time (or `--dev`). With no flags the latest (or `--version N`) profile is
    shown. `--set-reference N --roast ID` marks the version that tasted right.
    """
    history = load_history()
    title, slug = _bean_for(history, args.bean)

    if getattr(args, "set_reference", None):
        roast_id = resolve_roast_id(history, getattr(args, "roast", None))
        if not roast_id:
            print("Roast not found (give --roast <id> to say which batch tasted right).")
            return
        ref = set_reference(slug, args.set_reference, roast_id)
        print(f"Reference for {title}: v{ref['version']} ({roast_id}).")
        return

    from_roast = getattr(args, "from_roast", None)
    if from_roast:
        roast_id = resolve_roast_id(history, from_roast)
        if not roast_id:
            print("Roast not found.")
            return
        entry = history[roast_id]
        try:
            data = extract_roast_data(parse_alog(entry.get("source_file", "")))
        except (ValueError, FileNotFoundError, KeyError, IndexError, TypeError) as e:
            print(f"Could not re-read the roast's .alog: {e}")
            return
        env = envelope_for(history, title)
        metrics = entry.get("metrics", {})
        dev = getattr(args, "dev", None) or int(round(metrics.get("dev_phase_time") or 150))
        sched = schedule_from_timeline(build_control_timeline(data), metrics.get("fc_time", 0),
                                       env, dev_s=dev)
        res = validate_schedule(sched, env)
        if not res["ok"]:
            print("The roast's schedule does not pass the validator:")
            for e in res["errors"]:
                print(f"  !! {e}")
            return
        prof = new_profile(slug, title, res["schedule"],
                           based_on={"kind": "roast", "roast_id": roast_id,
                                     "borrowed_from": env.get("borrowed_from")},
                           validator={"errors": [], "warnings": res["warnings"], "clamped": res["clamped"]},
                           envelope_snapshot=compact_envelope(env))
        path = save_profile(prof)
        print(f"Saved {path}")
        print(display_profile(prof, env))
        return

    if getattr(args, "design", False):
        _design_version(history, title, slug, dev=getattr(args, "dev", None))
        return
    if getattr(args, "revise", False):
        _revise_version(history, title, slug, getattr(args, "version", None))
        return

    version = getattr(args, "version", None)
    prof = load_profile(slug, version)
    if not prof:
        have = list_versions(slug)
        if have:
            print(f"No version {version} for {title}; have {have}.")
        else:
            print(f"No profile for {title} yet. Design one with:  profile \"{title}\" --design"
                  f"  (or --from-roast <id> for a logged roast's schedule)")
        return
    env = prof.get("envelope_snapshot") or {}
    print(display_profile(prof, env))
    ref = get_reference(slug)
    if ref:
        print(f"Reference: v{ref['version']} ({ref['roast_id']})")


def _dossier_for(title, slug, research=False, alias=None, fetch=True):
    """The bean's dossier, built (find-coffee + supplier page) when missing."""
    doc = load_dossier(slug) or find_dossier_for_title(title)
    if doc and not research:
        return doc, "stored"
    if not doc:
        server_ok, server_status = ensure_server_running()
        coffee_data = None
        if server_ok:
            coffee_data, lookup_status = lookup_bean(title)
            if not coffee_data:
                print(f"  find-coffee: {lookup_status}")
        else:
            print(f"  find-coffee: {server_status}")
        doc = build_dossier(title, coffee_data, fetch_page=fetch)
        stop_server()
        if alias:
            add_alias(doc, alias)
        source = doc.get("specs_source")
        status = "built" + {"find-coffee": " (specs from find-coffee)",
                            "page": " (page fetched)"}.get(source, " (no page)")
    else:
        status = "stored"
    if research:
        rstatus = research_bean(doc)
        status += f", research {rstatus}"
    save_dossier(doc)
    return doc, status


def cmd_dossier(args):
    """Research a bean into beans/<slug>/dossier.json and show it."""
    history = load_history()
    title, slug = _bean_for(history, args.bean)
    doc, status = _dossier_for(title, slug, research=getattr(args, "research", False),
                               alias=getattr(args, "alias", None),
                               fetch=not getattr(args, "no_fetch", False))
    print(f"Dossier for {doc['name']} ({status}): {dossier_path_text(slug)}")
    rr = doc.get("roast_range")
    print(f"  Supplier roast range: {rr['min']} to {rr['max']}" if rr else "  Supplier roast range: not found")
    print(f"  Target for this operator: {doc.get('target_level')}")
    for key in ("process", "region", "variety", "altitude", "arrival"):
        if doc.get(key):
            print(f"  {key.capitalize()}: {doc[key]}")
    if doc.get("research"):
        print("  Research brief:")
        for line in doc["research"]["text"].splitlines():
            print("    " + line)
    print(display_bean_profile(doc))


def dossier_path_text(slug):
    from bean_dossier import dossier_path
    return str(dossier_path(slug))


def _prior_versions(slug):
    return [load_profile(slug, v) for v in list_versions(slug)]


def _save_design(title, slug, result, env, based_on, status, fallback_note=None):
    """Store a designer result (or the staircase fallback) as a new version."""
    prof = new_profile(
        slug, title, result["schedule"], based_on=based_on,
        designer={"model": LLM_MODEL, "status": status,
                  "repair_rounds": result.get("repair_rounds", 0)},
        predicted=result.get("predicted"),
        flavor_hypothesis=result.get("flavor_hypothesis", ""),
        contrast_lever=result.get("contrast_lever"),
        changes=result.get("changes_from_baseline") or result.get("validator_changes") or [],
        validator=result.get("validator"),
        envelope_snapshot=compact_envelope(env),
        watch_for=result.get("watch_for"),
    )
    prof["rationale"] = result.get("rationale", "")
    prof["roast_level_target"] = result.get("roast_level_target", "")
    if fallback_note:
        prof["validator"].setdefault("warnings", []).insert(0, fallback_note)
    path = save_profile(prof)
    print(f"Saved {path}")
    print(display_profile(prof, env))
    print(f"Next:  export-alarms \"{title}\"" + ("  (add --manual-drop for a first session)" if prof["version"] == 1 else ""))
    return prof


def _design_version(history, title, slug, dev=None):
    """`profile <bean> --design`: the designer writes the next version."""
    doc, dstatus = _dossier_for(title, slug)
    print(f"Dossier: {dstatus}")
    env = envelope_for(history, title)
    if env.get("borrowed_from"):
        print(f"No roasts of {title} here; machine envelope borrowed from {env['borrowed_from']}.")
    elif not env.get("median_curve"):
        print("No usable roast history on this machine: the designer has no envelope to work inside.")
        return
    prior = _prior_versions(slug)
    print(f"Designing with {LLM_MODEL}...")
    result, status = design_profile(doc, env, prior_versions=prior, dev_s=dev)
    if result:
        based_on = {"kind": "design", "borrowed_from": env.get("borrowed_from"),
                    "baseline": "measured staircase"}
        _save_design(title, slug, result, env, based_on, status)
        return
    print(f"  !! designer unavailable: {status}")
    sched = schedule_from_envelope(env, dev_s=dev or 150)
    res = validate_schedule(sched, env)
    if not res["ok"]:
        print("  The measured staircase does not validate either:")
        for e in res["errors"]:
            print(f"    !! {e}")
        return
    fallback = {"schedule": res["schedule"], "predicted": res["schedule"].get("predicted"),
                "validator": {"errors": [], "warnings": res["warnings"], "clamped": res["clamped"]}}
    _save_design(title, slug, fallback, env, {"kind": "staircase", "borrowed_from": env.get("borrowed_from")},
                 status, fallback_note=f"designer unavailable ({status}); this is the measured staircase")


def _revise_version(history, title, slug, version=None):
    """`profile <bean> --revise`: the next version from the cupping verdicts."""
    prof = load_profile(slug, version)
    if not prof:
        print(f"No profile for {title} to revise.")
        return
    verdicts = [dict(r.get("cupping_intake") or {}, batch=r.get("batch_nr"), dev_s=r.get("dev_s"),
                     notes=r.get("cupping_notes", ""), execution=r.get("execution"))
                for r in prof.get("roasts", []) if r.get("cupping_intake") or r.get("cupping_notes")]
    if not verdicts:
        print(f"v{prof['version']} has no cupped roasts yet; cup them first (cupping <batch> --intake).")
        return
    doc, _ = _dossier_for(title, slug)
    env = envelope_for(history, title)
    ref = get_reference(slug)
    reference = load_profile(slug, ref["version"]) if ref else None
    print(f"Revising v{prof['version']} from {len(verdicts)} verdict(s) with {LLM_MODEL}...")
    result, status = revise_profile(prof, doc, env, verdicts, prior_versions=_prior_versions(slug),
                                    reference=reference)
    if not result:
        print(f"  !! no revision: {status}")
        return
    changes = result.get("changes_from_baseline") or result.get("validator_changes") or []
    if not changes:
        print(f"The designer advises no change: roast v{prof['version']} again.")
        if result.get("rationale"):
            print("  " + result["rationale"])
        return
    based_on = {"kind": "revision", "version": prof["version"],
                "reference": ref["version"] if ref else None}
    _save_design(title, slug, result, env, based_on, status)


def _write_sheet_for(title, slug, prof, env, rows, alarm_file):
    html_text = render_sheet(prof, load_dossier(slug), env, rows,
                             batches_from_profile(prof, env), alarm_file=alarm_file)
    path = write_sheet(slug, prof["version"], html_text)
    print(f"Sheet: {path}")
    return path


def cmd_sheet(args):
    """Regenerate the printable session sheet for a profile version."""
    history = load_history()
    title, slug = _bean_for(history, args.bean)
    prof = load_profile(slug, getattr(args, "version", None))
    if not prof:
        print(f"No profile for {title}.")
        return
    env = envelope_for(history, title)
    if not env.get("median_curve"):
        env = prof.get("envelope_snapshot") or env
    exported = prof.get("exported") or {}
    rows, alarm_file = None, None
    if exported:
        res = schedule_to_alarms(prof["schedule"], env, mode=exported.get("mode", "paired"),
                                 ear_latency_s=exported.get("ear_latency_s") or 0,
                                 manual_drop=exported.get("manual_drop", False))
        rows = res["rows"] if not res["errors"] else None
        files = exported.get("files") or []
        alarm_file = Path(files[0]).name if files else None
    _write_sheet_for(title, slug, prof, env, rows, alarm_file)


def cmd_export_alarms(args):
    """Write the bean's profile as an Artisan alarm set (.alrm).

    Mode `paired` (default) gives every heater cut a BT trigger with its
    expected time as the fallback, `bt`/`time` one or the other. The FCs ->
    DROP row is offset by the ear's measured alert latency unless
    `--ear-latency` says otherwise; `--manual-drop` leaves it out for a
    first session where the operator drops by hand. `--install-local`
    also writes the table into this machine's Artisan settings for a
    Simulator run (Artisan must be closed).
    """
    history = load_history()
    title, slug = _bean_for(history, args.bean)
    version = getattr(args, "version", None)
    prof = load_profile(slug, version)
    if not prof:
        print(f"No profile for {title}. Build one with:  profile \"{title}\" --from-roast <id>")
        return
    env = envelope_for(history, title)
    if not env.get("median_curve"):
        env = prof.get("envelope_snapshot") or env
    latency_arg = getattr(args, "ear_latency", None)
    if latency_arg in (None, "auto"):
        latency = float(env.get("ear_latency_s") or 0)
    else:
        latency = float(latency_arg)
    mode = getattr(args, "mode", None)
    lever = prof.get("contrast_lever") or {}
    bracket = len({v for v in (lever.get("values") or []) if v is not None}) > 1
    if not mode:
        # A borrowed envelope's clock fallbacks come from another bean's
        # curve; a slower bean would get its cuts early. Temperature only.
        # A contrast session also gets BT-only rows: with paired rows a cold
        # first batch runs on the clock twins and a heat-soaked third on the
        # BT twins, so the pre-FC schedule would differ with roast order and
        # the bracket's one lever would no longer be the only difference.
        if env.get("borrowed_from"):
            mode = "bt"
            print(f"  Envelope borrowed from {env['borrowed_from']}: exporting BT-triggered rows "
                  "without clock fallbacks (the first session calibrates this bean's timing)")
        elif bracket:
            mode = "bt"
            print(f"  Contrast set on {lever.get('key', 'dev_seconds')} {lever.get('values')}: exporting "
                  "BT-triggered rows only, so every batch gets the same cuts at the same BT "
                  "(--mode paired to override)")
        else:
            mode = "paired"
    manual_drop = bool(getattr(args, "manual_drop", False))
    if manual_drop and latency:
        # The operator's own mark times the drop; the ear's latency only
        # applies to a DROP row run from the ear's mark.
        latency = 0.0
    warmup = not getattr(args, "no_warmup", False)
    cool = getattr(args, "cool", None)
    cool = 240 if cool is None else int(cool)
    res = schedule_to_alarms(prof["schedule"], env, mode=mode, ear_latency_s=latency,
                             manual_drop=manual_drop, warmup=warmup, cool_after_s=cool)
    for w in res["warnings"]:
        print(f"  ! {w}")
    if res["errors"]:
        for e in res["errors"]:
            print(f"  !! {e}")
        print("Not exported.")
        return
    _warn_if_quantifiers_active()
    suffix = "" if mode == "paired" else f"-{mode}"
    path = alarm_path(slug, prof["version"], suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_alrm(path, res["rows"])
    mark_exported(prof, mode, latency, manual_drop, [path])
    save_profile(prof)
    print(display_alarm_rows(res["rows"], f"Alarm set: {title} v{prof['version']} ({mode})"))
    print(f"Written: {path}")
    _write_sheet_for(title, slug, prof, env, res["rows"], path.name)
    print("On the roaster: Config -> Alarms -> Load this file, then ON. The first row takes Hottop control; "
          "never press CONTROL yourself.")
    if manual_drop:
        print("DROP is manual for this set: you mark FCs and press DROP at FC + dev yourself.")
    else:
        print("The ear marks FCs for this set (run it with --push-artisan); keep your hands off the FCs button.")
    if getattr(args, "install_local", False):
        _install_local_alarms(res["rows"])


def _warn_if_quantifiers_active(conf_path=None):
    """Artisan's event quantifiers re-log the Hottop's smoothed heater/fan
    echo as slider moves after an alarm-driven cut (an alarm move does not
    set the 45 s quantification block a hand move does), bouncing the slider
    and filling the log with phantom moves. They must be off wherever the
    alarm set runs."""
    from artisan_conf import ArtisanConf
    path = Path(conf_path) if conf_path else PROJECT_DIR / "artisan" / "Artisan.conf"
    if not path.exists():
        return
    active = ArtisanConf.load(path).get_list("Quantifiers", "quantifieractive") or []
    if any(str(v).strip() not in ("0", "") for v in active):
        print(f"  ! event quantifiers are active in {path.name} ({', '.join(active)}); "
              "set [Quantifiers] quantifieractive to 0, 0, 0, 0 and push, or every alarm cut "
              "is followed by phantom Q moves")


def _install_local_alarms(rows):
    """Write rows into this machine's Artisan.conf [Alarms] for a Simulator run."""
    import subprocess
    from artisan_conf import ArtisanConf
    conf_path = Path.home() / ".config" / "artisan-scope" / "Artisan.conf"
    if not conf_path.exists():
        print(f"No local Artisan settings at {conf_path}; run artisan/deploy.sh local first.")
        return
    running = subprocess.run(["pgrep", "-f", "share/artisan/artisan"], capture_output=True)
    if running.returncode == 0:
        print("Artisan is running here; close it first (it rewrites its settings on exit).")
        return
    conf = ArtisanConf.load(conf_path)
    rows_to_conf(conf, rows)
    conf.save(conf_path)
    print(f"Installed {len(rows)} alarm rows into {conf_path}; start Artisan to load them.")
    _warn_if_quantifiers_active(conf_path)


def main():
    parser = argparse.ArgumentParser(
        description="Coffee roast analysis tool for Hottop KN-8828B-2K+",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python analyze.py full          # Full analysis of latest roast\n"
               "  python analyze.py show 1        # Show batch #1\n"
               "  python analyze.py bean Ethiopia  # Look up a bean\n"
               "  python analyze.py cupping 1 --intake   # guided tasting questions\n"
               "  python analyze.py plan --dev 150,90,210  # next session's contrast set\n",
    )
    parser.add_argument("--debug", action="store_true", help="Show full traceback on errors")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # scan
    p_scan = subparsers.add_parser("scan", help="Analyze new .alog files")
    p_scan.add_argument("--force", action="store_true", help="Re-analyze all files")

    # show
    p_show = subparsers.add_parser("show", help="Show roast summary")
    p_show.add_argument("roast_id", nargs="?", default=None, help="Roast ID or batch number")

    # compare
    p_compare = subparsers.add_parser("compare", help="Compare two roasts")
    p_compare.add_argument("id1", nargs="?", default=None, help="First roast ID")
    p_compare.add_argument("id2", nargs="?", default=None, help="Second roast ID")

    # recommend
    p_recommend = subparsers.add_parser("recommend", help="Recommendations for latest roast")
    p_recommend.add_argument("roast_id", nargs="?", default=None, help="Roast ID or batch number")
    p_recommend.add_argument("--verbose", "-v", action="store_true", help="Show full cupping notes")

    # cupping
    p_cupping = subparsers.add_parser("cupping", help="Add/view cupping notes")
    p_cupping.add_argument("roast_id", help="Roast ID or batch number")
    p_cupping.add_argument("--notes", "-n", help="Cupping notes text")
    p_cupping.add_argument("--intake", "-i", action="store_true",
                           help="Guided cupping questions (sour/roasty, astringency, sweetness, preference)")
    p_cupping.add_argument("--intake-json", help="Intake answers as a JSON object (for scripts)")

    # plan
    p_plan = subparsers.add_parser("plan", help="Contrast set for the next session (vary FC->DROP)")
    p_plan.add_argument("roast_id", nargs="?", default=None, help="Anchor roast ID or batch number (default: latest)")
    p_plan.add_argument("--dev", help="FC->DROP seconds per batch, comma-separated, in roasting order (default 150,90,210)")
    p_plan.add_argument("--profile", action="store_true", help="Use the bean's latest designed profile as the recipe")

    # full
    p_full = subparsers.add_parser("full", help="Full analysis: scan + show + recommend")
    p_full.add_argument("roast_id", nargs="?", default=None, help="Roast ID or batch number")
    p_full.add_argument("--force", action="store_true", help="Re-analyze all files")
    p_full.add_argument("--verbose", "-v", action="store_true", help="Show full cupping notes")

    # list
    subparsers.add_parser("list", help="List all analyzed roasts")

    # bean
    p_bean = subparsers.add_parser("bean", help="Look up a bean in find-coffee")
    p_bean.add_argument("name", help="Bean name to search for")

    # profile
    p_profile = subparsers.add_parser("profile", help="Show or build a bean's roast profile")
    p_profile.add_argument("bean", help="Bean name (matches history titles; a new name is a new bean)")
    p_profile.add_argument("--design", action="store_true",
                           help="The designer writes the next version from the dossier and the machine envelope")
    p_profile.add_argument("--revise", action="store_true",
                           help="The designer writes the next version from the cupping verdicts")
    p_profile.add_argument("--from-roast", help="Build a version from this roast's logged moves (no LLM)")
    p_profile.add_argument("--dev", type=int, help="FC->DROP seconds for --from-roast / --design baseline")
    p_profile.add_argument("--version", type=int, help="Show this version instead of the latest")
    p_profile.add_argument("--set-reference", type=int, metavar="N",
                           help="Mark version N as the bean's reference (with --roast ID)")
    p_profile.add_argument("--roast", help="The roast that tasted right, for --set-reference")

    # dossier
    p_dossier = subparsers.add_parser("dossier", help="Research a bean into beans/<slug>/dossier.json")
    p_dossier.add_argument("bean", help="Bean name (find-coffee name or Artisan title)")
    p_dossier.add_argument("--research", action="store_true", help="Also ask the model to research it on the web")
    p_dossier.add_argument("--alias", help="An Artisan roast title that names this bean")
    p_dossier.add_argument("--no-fetch", action="store_true", help="Skip the supplier page fetch")

    # sheet
    p_sheet = subparsers.add_parser("sheet", help="Regenerate the printable session sheet")
    p_sheet.add_argument("bean", help="Bean name")
    p_sheet.add_argument("--version", type=int, help="Profile version (default: latest)")

    # export-alarms
    p_alarms = subparsers.add_parser("export-alarms", help="Artisan alarm set from the bean's profile")
    p_alarms.add_argument("bean", help="Bean name")
    p_alarms.add_argument("--version", type=int, help="Profile version (default: latest)")
    p_alarms.add_argument("--mode", choices=["paired", "bt", "time"], default=None,
                          help="BT trigger with time fallback (default), BT only (default for a "
                               "borrowed envelope), or time only")
    p_alarms.add_argument("--manual-drop", action="store_true",
                          help="No FCs-timed DROP row (operator drops by hand); the BT ceiling stays")
    p_alarms.add_argument("--ear-latency", default="auto",
                          help="Seconds subtracted from the DROP offset (default: measured from the ear)")
    p_alarms.add_argument("--no-warmup", action="store_true", help="Leave out the From-ON warm-up rows")
    p_alarms.add_argument("--cool", type=int, help="COOL END this many seconds after DROP (default 240; 0 = none)")
    p_alarms.add_argument("--install-local", action="store_true",
                          help="Also write the table into this machine's Artisan settings (Simulator)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Dispatch to command handler
    commands = {
        "scan": cmd_scan,
        "show": cmd_show,
        "compare": cmd_compare,
        "recommend": cmd_recommend,
        "cupping": cmd_cupping,
        "plan": cmd_plan,
        "full": cmd_full,
        "list": cmd_list,
        "bean": cmd_bean,
        "profile": cmd_profile,
        "export-alarms": cmd_export_alarms,
        "dossier": cmd_dossier,
        "sheet": cmd_sheet,
    }

    handler = commands.get(args.command)
    if handler:
        try:
            handler(args)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            sys.exit(130)
        except Exception as e:
            print(f"Error: {e}")
            if getattr(args, "debug", False):
                traceback.print_exc()
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
