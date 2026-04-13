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
    python analyze.py full [roast_id]   # Everything: scan + summary + recommendations
    python analyze.py list              # List all analyzed roasts
    python analyze.py bean <name>       # Look up a bean in find-coffee
"""

import argparse
import json
import sys
from pathlib import Path

from roast_parser import parse_alog, extract_roast_data, scan_roast_logs
from roast_metrics import extract_metrics, compare_to_targets
from roast_analysis import analyze_roast, compare_roasts, trend_analysis, generate_next_roast_summary
from roast_display import (
    display_roast_summary,
    display_bean_profile,
    display_target_comparison,
    display_recommendations,
    display_roast_comparison,
    display_trend,
    display_roast_list,
    display_next_roast,
)
from coffee_lookup import lookup_bean, extract_bean_profile, ensure_server_running, stop_server
from sentinel_loader import match_sentinel_to_roast, extract_visual_data, enrich_trajectory_with_temps

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
    """Save analysis history to disk."""
    HISTORY_FILE.write_text(json.dumps(history, indent=2, default=str))


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

    # Try partial name match
    for rid in history:
        if roast_id.lower() in rid.lower():
            return rid

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
        # Parse the file
        raw = parse_alog(alog_path)
        data = extract_roast_data(raw)
        roast_id = data["roast_id"]

        # Skip if already analyzed (unless --force)
        if roast_id in history and not getattr(args, "force", False):
            print(f"  Already analyzed: {roast_id}")
            continue

        # Look up bean profile from find-coffee
        bean_profile = None
        bean_name = data.get("title", "")
        if bean_name and server_ok:
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

        # Run analysis
        analysis = analyze_roast(data, bean_profile, visual_data)
        analysis["source_file"] = str(alog_path)
        history[roast_id] = analysis
        new_count += 1
        print(f"  Analyzed: {roast_id}")
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

    # Resolve IDs (default to last two)
    id1 = resolve_roast_id(history, args.id1)
    id2 = resolve_roast_id(history, args.id2)

    if not id1 or not id2:
        # Default to the two most recent
        id1 = analyses[-2]["roast_id"]
        id2 = analyses[-1]["roast_id"]

    if id1 not in history or id2 not in history:
        print("One or both roast IDs not found.")
        return

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
    print(display_target_comparison(analysis.get("comparisons", []), analysis.get("metrics", {})))
    print()
    print(display_recommendations(analysis.get("recommendations", []), verbose=verbose))

    # Next roast synthesis
    actions = generate_next_roast_summary(
        analysis.get("comparisons", []),
        analysis.get("metrics", {}),
        analysis.get("recommendations", []),
    )
    if actions:
        print()
        print(display_next_roast(actions))


def cmd_cupping(args):
    """Add or update cupping notes for a roast."""
    history = load_history()
    roast_id = resolve_roast_id(history, args.roast_id)

    if not roast_id:
        print("Roast not found.")
        return

    if args.notes:
        history[roast_id]["cupping_notes"] = args.notes
        save_history(history)
        print(f"Updated cupping notes for {roast_id}:")
        print(f"  \"{args.notes}\"")
    else:
        current = history[roast_id].get("cupping_notes", "")
        if current:
            print(f"Current notes: \"{current}\"")
        else:
            print("No cupping notes. Use --notes to add them.")


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

    # Target comparison
    print()
    print(display_target_comparison(analysis.get("comparisons", []), analysis.get("metrics", {})))

    # Recommendations
    verbose = getattr(args, "verbose", False)
    print()
    print(display_recommendations(analysis.get("recommendations", []), verbose=verbose))

    # Next roast synthesis
    actions = generate_next_roast_summary(
        analysis.get("comparisons", []),
        analysis.get("metrics", {}),
        analysis.get("recommendations", []),
    )
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


def main():
    parser = argparse.ArgumentParser(
        description="Coffee roast analysis tool for Hottop KN-8828B-2K+",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python analyze.py full          # Full analysis of latest roast\n"
               "  python analyze.py show 1        # Show batch #1\n"
               "  python analyze.py bean Ethiopia  # Look up a bean\n"
               "  python analyze.py cupping 1 --notes 'Bright berry, clean finish'\n",
    )

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
        "full": cmd_full,
        "list": cmd_list,
        "bean": cmd_bean,
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
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
